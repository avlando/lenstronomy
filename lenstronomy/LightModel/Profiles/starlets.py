__author__ = 'aymgal'

import numpy as np

from lenstronomy.LightModel.Profiles import starlets_slit
from lenstronomy.LightModel.Profiles.interpolation import Interpol

import slitronomy.Util.util as util_s 

_force_no_pysap = False  # for debug only


class Starlets(object):
    """
    Implementation of the Isotropic Undecimated Walevet Transform (aka "starlet", or "B-spline") 
    using the 'a trous' algorithm.

    Astronomical data (galaxies, stars, ...) are sparse when expressed in starlet basis.

    Based on Starck et al. : https://ui.adsabs.harvard.edu/abs/2007ITIP...16..297S/abstract
    """
    param_names = ['coeffs', 'n_scales', 'n_pixels']
    lower_limit_default = {'coeffs': [0], 'n_scales': 2, 'n_pixels': 10}
    upper_limit_default = {'coeffs': [1e8], 'n_scales': 20, 'n_pixels': 1e10}

    def __init__(self, thread_count=1, fast_inverse=True, second_gen=False, show_pysap_plots=False):
        """
        Load pySAP package if found, and initialize the Starlet transform.

        :param thread_count: number of threads used for pySAP computations
        :param fast_inverse: if True, reconstruction is simply the sum of each scale (only for 1st generation starlet transform)
        :param second_gen: if True, uses the second generation of starlet transform 
        :param show_pysap_plots: if True, displays pySAP plots when calling the decomposition method
        """        
        self.use_pysap, pysap = self._load_pysap()
        if self.use_pysap:
            self._transf_class = pysap.load_transform('BsplineWaveletTransformATrousAlgorithm')
        self._thread_count = thread_count
        self._fast_inverse = fast_inverse
        self._second_gen = second_gen
        self._show_pysap_plots = show_pysap_plots
        self.interpol = Interpol()

    def function(self, x, y, coeffs=None, n_scales=None, n_pixels=None, scale=1, center_x=0, center_y=0):
        """
        1D inverse starlet transform from starlet coefficients stored in coeffs
        Follows lenstronomy conventions for light profiles.

        :param coeffs: decomposition coefficients, 
        ndarray with shape (n_scales, sqrt(n_pixels), sqrt(n_pixels)) or (n_scales*n_pixels,)
        :param n_scales: number of decomposition scales
        :param n_pixels: number of pixels in a single scale
        :return: reconstructed signal as 1D array of shape (n_pixels,)
        """
        if len(coeffs.shape) == 1:
            coeffs = util_s.array2cube(coeffs, n_scales, n_pixels)
        image = self.function_2d(coeffs, n_scales, n_pixels)
        image = self.interpol.function(x, y, image=image, scale=scale,
                                       center_x=center_x, center_y=center_y,
                                       amp=1, phi_G=0)
        return image

    def function_2d(self, coeffs, n_scales, n_pixels):
        """
        2D inverse starlet transform from starlet coefficients stored in coeffs

        :param coeffs: decomposition coefficients, 
        ndarray with shape (n_scales, sqrt(n_pixels), sqrt(n_pixels))
        :param n_scales: number of decomposition scales
        :param n_pixels: number of pixels in a single scale
        :return: reconstructed signal as 2D array of shape (n_pixels,)
        """
        if self.use_pysap:
            return self._inverse_transform(coeffs, n_scales)
        else:
            return starlets_slit.inverse_transform(coeffs, fast=self._fast_inverse, 
                                                   second_gen=self._second_gen)

    def decomposition(self, image, n_scales):
        """
        1D starlet transform from starlet coefficients stored in coeffs

        :param image: 2D image to be decomposed, ndarray with shape (sqrt(n_pixels), sqrt(n_pixels))
        :param n_scales: number of decomposition scales
        :return: reconstructed signal as 1D array of shape (n_scales*n_pixels,)
        """
        return util_s.cube2array(self.decomposition_2d(image, n_scales))

    def decomposition_2d(self, image, n_scales):
        """
        2D starlet transform from starlet coefficients stored in coeffs

        :param image: 2D image to be decomposed, ndarray with shape (sqrt(n_pixels), sqrt(n_pixels))
        :param n_scales: number of decomposition scales
        :return: reconstructed signal as 2D array of shape (n_scales, sqrt(n_pixels), sqrt(n_pixels))
        """
        if self.use_pysap:
            coeffs = self._transform(image, n_scales)
        else:
            coeffs = starlets_slit.transform(image, n_scales, second_gen=self._second_gen)
        return coeffs

    def spectral_norm(self, n_scales, n_pixels):
        """
        spectral norm associated to the starlet transform operator

        :param num_pix: number of side pixels of a test image for computing the norm
        :param n_scales: number of decomposition scales
        :return: spectral norm
        """
        if not hasattr(self, '_spectral_norm') or n_scales != self._n_scales:
            self._spectral_norm = self._compute_spectral_norm(n_scales, n_pixels, num_iter=20, tol=1e-10)
            self._n_scales = n_scales
        return self._spectral_norm

    def _inverse_transform(self, coeffs, n_scales):
        """reconstructs image from starlet coefficients"""
        self._check_transform_pysap(n_scales)
        if self._fast_inverse and not self._second_gen:
            # for 1st gen starlet the reconstruction can be performed by summing all scales 
            image = np.sum(coeffs, axis=0)
        else:
            coeffs = self._coeffs2pysap(coeffs)
            self._transf.analysis_data = coeffs
            result = self._transf.synthesis()
            if self._show_pysap_plots:
                result.show()
            image = result.data
        return image

    def _transform(self, image, n_scales):
        """decomposes an image into starlets coefficients"""
        self._check_transform_pysap(n_scales)
        self._transf.data = image
        self._transf.analysis()
        if self._show_pysap_plots:
            self._transf.show()
        coeffs = self._transf.analysis_data
        coeffs = self._pysap2coeffs(coeffs)
        return coeffs

    def _check_transform_pysap(self, n_scales):
        """if needed, update the loaded pySAP transform to correct number of scales"""
        if not hasattr(self, '_transf') or n_scales != self._n_scales:
            self._transf = self._transf_class(nb_scale=n_scales, verbose=False, 
                                              nb_procs=self._thread_count)
            self._n_scales = n_scales
        # if getattr(self._transf, 'nb_band_per_scale', 0) is None:
        #     self._transf.nb_band_per_scale = [1]*self._n_scales  # dirty patch to PySAP bug

    def _compute_spectral_norm(self, n_scales, n_pixels, num_iter=20, tol=1e-10):
        """compute spectral norm of the starlet operator"""
        operator = lambda x: self.decomposition_2d(x, n_scales)
        inverse_operator = lambda c: self.function_2d(c, n_scales, n_pixels)
        num_pix = int(np.sqrt(n_pixels))
        return util_s.spectral_norm(num_pix, operator, inverse_operator, num_iter=num_iter, tol=tol)

    def _pysap2coeffs(self, coeffs):
        """convert pySAP decomposition coefficients to numpy array"""
        return np.asarray(coeffs)

    def _coeffs2pysap(self, coeffs):
        """convert coefficients stored in numpy array to list required by pySAP"""
        coeffs_list = []
        for i in range(coeffs.shape[0]):
            coeffs_list.append(coeffs[i, :, :])
        return coeffs_list

    def _load_pysap(self):
        """load pySAP module"""
        if _force_no_pysap:
            return False, None
        try:
            import pysap
        except ImportError:
            return False, None
        else:
            return True, pysap
