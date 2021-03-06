# -*- coding: utf-8 -*-

"""
    
/***************************************************************************
 DemShading - Terrain position algorithm
 This algorithm caluclates relative topographic position of each pixel of an
 elevation model (higher/lower than the neighbourhood) 
                              -------------------
        begin                : 2020-02-20
        copyright            : (C) 2020 by Zoran Čučković
 ***************************************************************************/
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = 'Zoran Čučković'
__date__ = '2020-02-05'
__copyright__ = '(C) 2020 by Zoran Čučković'

from os import sys, path

from PyQt5.QtCore import QCoreApplication
from qgis.core import (QgsProcessing,
                       QgsProcessingException,
                       QgsProcessingAlgorithm,
                       QgsProcessingParameterRasterLayer,
                       QgsProcessingParameterRasterDestination,
                        QgsProcessingParameterBoolean,
                      QgsProcessingParameterNumber,
                       QgsProcessingParameterEnum,
                       QgsProcessingUtils,
                        QgsRasterBandStats,
                       QgsSingleBandGrayRenderer,
                       QgsContrastEnhancement)

from processing.core.ProcessingConfig import ProcessingConfig

import gdal
import numpy as np

from .modules.helpers import view, window_loop, filter3

from qgis.core import QgsMessageLog # for testing

class TpiAlgorithm(QgsProcessingAlgorithm):
    """
    This algorithm simulates ambient lighting over a raster DEM (in input). 
    """

    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.

    INPUT = 'INPUT'
    RADIUS= 'RADIUS'
    DENOISE = 'DENOISE'
    ANALYSIS_TYPE='ANALYSIS_TYPE'
    OUTPUT = 'OUTPUT'

    ANALYSIS_TYPES = ['Simple', 'Height weighted', 'Distance weighted']

    output_model = None #for post-processing

    def initAlgorithm(self, config):
        """
        Here we define the inputs and output of the algorithm, along
        with some other properties.
        """

        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT,
                self.tr('Digital elevation model')
            ) )
        
        self.addParameter(QgsProcessingParameterEnum (
            self.ANALYSIS_TYPE,
            self.tr('Analysis type'),
            self.ANALYSIS_TYPES,
            defaultValue=0))
                    
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS,
            self.tr('Radius (pixels)'),
            0, # QgsProcessingParameterNumber.Integer = 0
            5, False, 0, 100))

        self.addParameter(QgsProcessingParameterBoolean(
            self.DENOISE,
            self.tr('Denoise'),
            False, False)) 
        
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT,
            self.tr("Topographic position index")))
        
    def processAlgorithm(self, parameters, context, feedback):
    
            
        elevation_model= self.parameterAsRasterLayer(parameters,self.INPUT, context)

        if elevation_model.crs().mapUnits() != 0 :
            err= " \n ****** \n ERROR! \n Raster data has to be projected in a metric system!"
            feedback.reportError(err, fatalError = False)
           # raise QgsProcessingException(err)
        #could also use:
        #raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        if  round(abs(elevation_model.rasterUnitsPerPixelX()),
                    2) !=  round(abs(elevation_model.rasterUnitsPerPixelY()),2):
            
            err= (" \n ****** \n ERROR! \n Raster pixels are irregular in shape " +
                  "(probably due to incorrect projection)!")
            feedback.reportError(err, fatalError = False)
            #raise QgsProcessingException(err)

        self.output_model = self.parameterAsOutputLayer(parameters,self.OUTPUT,context)

        denoise = self.parameterAsInt(parameters,self.DENOISE, context)        
       
        radius =self.parameterAsInt(parameters,self.RADIUS, context)

        weighted = self.parameterAsInt(parameters,self.ANALYSIS_TYPE, context)
        
        overlap = radius if not denoise else radius +1

        dem = gdal.Open(elevation_model.source())
          
        # ! attention: x in gdal is y dimension un numpy (the first dimension)
        xsize, ysize = dem.RasterXSize,dem.RasterYSize
        # assuming one band dem !
        nodata = dem.GetRasterBand(1).GetNoDataValue()
        
        pixel_size = dem.GetGeoTransform()[1]
        
   
        chunk = int(ProcessingConfig.getSetting('DATA_CHUNK')) * 1000000
        chunk = min(chunk // xsize, xsize)

        
        # writing output to dump data chunks
        driver = gdal.GetDriverByName('GTiff')
        ds = driver.Create(self.output_model, xsize,ysize, 1, gdal.GDT_Float32)
        ds.SetProjection(dem.GetProjection())
        ds.SetGeoTransform(dem.GetGeoTransform())
        
             
        chunk_slice = (ysize, chunk + 2 * overlap) 
        
        # define empty matrices to hold data : faster
        mx_z = np.zeros( chunk_slice)
        mx_a = np.zeros(mx_z.shape)
        
        if weighted ==1 :
            mx_cnt = np.zeros(mx_z.shape)    
            
        else: 
            #pre-calculate number of visits per cell 
            sy, sx = mx_z.shape
            c1, c2 = np.mgrid[0 : sy, 0 : sx]
            
            if weighted == 2:
                c1, c2 = np.cumsum(c1, axis = 0), np.cumsum(c2, axis=1)
                max_val = sum([i for i in range(radius+1)])
            
            else: 
                max_val = radius
                         
            c1, c2 = np.clip(c1, 0, max_val ), np.clip(c2, 0, max_val )
            
            # reverse and find distances to back edges
            np.minimum(c1, c1[::-1,:], c1); np.minimum(c2, c2[:, ::-1], c2)
           
            # corner = 3 * radius pixels (we have a star shaped window)
            mx_cnt =  max_val * 3 + c1*2 + c2*2 + np.minimum (c1, c2) 
            
          

        counter = 0

        
        #Loop through data chunks (and write results)
        for mx_view_in, gdal_take, mx_view_out, gdal_put in window_loop ( 
            shape = (xsize, ysize), 
            chunk = chunk,
            overlap = overlap) :

            mx_a[:]=0
            if weighted == 1: mx_cnt[:]=0   
            
            mx_z[mx_view_in]= dem.ReadAsArray(*gdal_take).astype(float)
            
            if denoise : mx_z = filter3(mx_z)
                
            # step thourgh 8 standard directions (N, NE, E, etc)
            # for more directions : step = 0.5; 0.25; etc
            # !! we use mirror values, to optimise !!
            for dx, dy in [(0,1), (1,1), (1,0), (1, -1)]:

                for r in range (1, radius + 1):     
                    # ! analyse only the supplied data : mx_z[mx_view_in]
                    view_in, view_out = view(r * dy, r * dx, mx_z[mx_view_in].shape)
                    # this is for readability only
                    view_out2, view_in2 = view_in, view_out
                    
                    z, z2 = mx_z[view_in], mx_z[view_in2] 
                    
                    # use distance (r) or height diffrence as weight
                    if weighted :
                        if weighted == 1 :
                            w = abs (z - z2)
                            # cannot precalculate these weights
                            mx_cnt[view_out] += w
                            mx_cnt[view_out2] += w
                            
                        else:  w = r 
                    # x1 should not introduce significant overhead, in theory...
                    else : w =1
                    
                    mx_a[view_out] += z * w 
                    mx_a[view_out2] += z2 * w 
                        
                counter += 1
                
                prog = chunk * (counter/8) /  xsize
                feedback.setProgress(100 * prog)
                if feedback.isCanceled(): sys.exit()
            
            # this is a patch : last chunk is often spilling outside raster edge 
            # so, move the edge values to match raster edge
            end = gdal_take[2]           
            if weighted != 1 and end + gdal_take[0] == xsize : 
                mx_cnt[:, end -radius : end] = mx_cnt[ : , -radius : ]
            
            mx_z -=  mx_a / mx_cnt # weighted mean !
            out = mx_z
            ds.GetRasterBand(1).WriteArray(out[mx_view_out], * gdal_put[:2])

        ds = None
        
        return {self.OUTPUT: self.output_model}

    def postProcessAlgorithm(self, context, feedback):

        output = QgsProcessingUtils.mapLayerFromString(self.output_model, context)
        provider = output.dataProvider()

        stats = provider.bandStatistics(1,QgsRasterBandStats.All,output.extent(),0)
        mean, sd = stats.mean, stats.stdDev
        
        rnd = QgsSingleBandGrayRenderer(provider, 1)
        ce = QgsContrastEnhancement(provider.dataType(1))
        ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)

        ce.setMinimumValue(mean-2*sd)
        ce.setMaximumValue(min(1, mean+2*sd))

        rnd.setContrastEnhancement(ce)

        output.setRenderer(rnd)
        
        output.triggerRepaint()

        return {self.OUTPUT: self.output_model}

    def name(self):
        """
        Returns the algorithm name, used for identifying the algorithm. This
        string should be fixed for the algorithm, and must not be localised.
        The name should be unique within each provider. Names should contain
        lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return 'Topographic position (TPI)'

    def displayName(self):
        """
        Returns the translated algorithm name, which should be used for any
        user-visible display of the algorithm name.
        """
        return self.tr(self.name())

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def shortHelpString(self):
        curr_dir = path.dirname(path.realpath(__file__))
        h = ( """
             Topographic position index is expressing the relative height of each elevation point within a specified radius. 
             
            <b>Input</b> should be an elevation model in raster format. 
            
           
            <b>Radius</b> is determing the search radius (in pixels).

            There are 3 <b>analysis types</b>: 1) standard TPI, 2) distance weighted and 3) height weighted. Weighted options use elevation point distance or height discrepancy as weighting factor.   
            
            <b>Denoise</b> option is applying a simple 3x3 smooth filter. 
            
            If you find this tool useful, consider to :
                 
             <a href='https://ko-fi.com/D1D41HYSW' target='_blank'><img height='30' style='border:0px;height:36px;' src='%s/help/kofi2.webp' /></a>
            """) % curr_dir
		
        return self.tr(h)

    def createInstance(self):
        return TpiAlgorithm()
