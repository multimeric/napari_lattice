#UI for reading files, deskewing and cropping

import os
from pathlib import Path

from magicclass.wrappers import set_design
from magicgui.widgets._bases.container_widget import ContainerWidget
from magicgui import magicgui
from magicclass import magicclass, click, field

import numpy as np
from napari.types import ImageData
from napari_plugin_engine import napari_hook_implementation
from napari.utils import progress

from llsz.array_processing import get_deskew_arr
from llsz.transformations import deskew_zeiss
from llsz.io import LatticeData
import tifffile

@magicclass(widget_type="scrollable", name ="LLSZ analysis")
class LLSZWidget:
    
    @magicclass(widget_type="list", popup_mode="last")#, close_on_run=False
    class llsz_menu:
        @set_design(background_color="orange", font_family="Consolas")
        def Open_File(self, path:Path):
            print("Opening", path)
            self.lattice=LatticeData(path, 30.0, "Y")
            self.aics = self.lattice.data
            self.file_name = os.path.splitext(os.path.basename(path))[0]
            self.save_name=os.path.splitext(os.path.basename(path))[0]
            self.parent_viewer.add_image(self.aics.dask_data)
            self["Open_File"].background_color = "green" 

        time_deskew = field(int, options={"min": 0,  "step": 1},name = "Time")
        chan_deskew = field(int, options={"min": 0,  "step": 1},name = "Channels")
        #set_design(text="Preview Deskew")
        @magicgui(header=dict(widget_type="Label",label="<h3>Preview Deskew</h3>"), call_button = "Preview")
        def Preview_Deskew(self, header, img_data:ImageData):#, img_layer:ImageData):
            print("Previewing deskewed channel and time")
            #Function for previewing deskew
            #time_deskew = field(int, options={"min": 0, "max": self.lattice.time,"step": 1},name = "Time")
            #chan_deskew = field(int, options={"min": 0, "max": self.lattice.channels, "step": 1},name = "Channels")
            #print(self.time_deskew.value,self.time_deskew.value)
            assert self.time_deskew.value < self.lattice.time, "Time is out of range"
            assert self.chan_deskew.value < self.lattice.channels, "Channel is out of range"
            time_deskew = self.time_deskew.value
            chan_deskew = self.chan_deskew.value
            #stack=self.aics
            angle=self.lattice.angle
            
            shear_factor = self.lattice.shear_factor
            scaling_factor = self.lattice.scaling_factor
            
            assert str.upper(self.lattice.skew) in ('Y','X'), "Skew direction not recognised. Enter either Y or X"
            #curr_time=viewer.dims.current_step[0]
            
            #self.lattice.deskew_shape,self.lattice.deskew_vol_shape,self.lattice.deskew_translate_y,self.lattice.deskew_z_start,self.lattice.deskew_z_end=process_czi(stack,angle,self.lattice.skew)
            print("Deskewing for Time:",time_deskew,"and Channel", chan_deskew )

            #print("Processing ",viewer.dims.current_step[0])
            
            #Get a dask array with same shape as final deskewed image and containing the raw data (Essentially a scaled up version of the raw data)   
            deskew_img=get_deskew_arr(self.aics.dask_data, self.lattice.deskew_shape, self.lattice.deskew_vol_shape, time= time_deskew, channel=chan_deskew, scene=0, skew_dir=self.lattice.skew)
            
            #Perform deskewing on the skewed dask array 
            deskew_full=deskew_zeiss(deskew_img,angle,shear_factor,scaling_factor,self.lattice.deskew_translate_y,reverse=False,dask=False)

            #Crop the z slices to get only the deskewed array and not the empty area
            deskew_final=deskew_full[self.lattice.deskew_z_start:self.lattice.deskew_z_end].astype('uint16') 

            #Add layer for cropping
            #viewer.add_shapes(shape_type='polygon', edge_width=5,edge_color='white',face_color=[1,1,1,0],name="Cropping BBOX layer")
            max_proj_deskew=np.max(deskew_final,axis=0)

            #add channel and time information to the name
            suffix_name = "_c"+str(chan_deskew)+"_t"+str(time_deskew)

            self.parent_viewer.add_image(max_proj_deskew,name="Deskew_MIP")
                                        
            #img_name="Deskewed image_c"+str(chan_deskew)+"_t"+str(time_deskew)
            self.parent_viewer.add_image(deskew_final,name="Deskewed image"+suffix_name)

            #return (deskew_full, {"name":"Uncropped data"})
            #(deskew_final, {"name":img_name})
        
        @magicgui(header=dict(widget_type="Label",label="<h3>Saving Data</h3>"),
                   time_start = dict(label="Time Start:"),
                   time_end = dict(label="Time End:" ),
                   ch_start = dict(label="Channel Start:"),
                   ch_end = dict(label="Channel End:", value =1 ),
                   save_path = dict(mode ='d',label="Directory to save "))
        def Deskew_Save(self, header, time_start:int, time_end:int, ch_start:int, ch_end:int, save_path:Path):
            assert time_start>=0, "Time start should be >0"
            assert time_end < self.lattice.time and time_end >0, "Check time entry "
            assert ch_start >= 0, "Channel start should be >0"
            assert ch_end <= self.lattice.channels and ch_end >= 0 , "Channel end should be less than "+str(self.lattice.channels)
            print(time_start)
            print(time_end)
            print(ch_start)
            print(ch_end)
            time_range = range(time_start, time_end)
            channel_range = range(ch_start, ch_end)
            angle=self.lattice.angle
            shear_factor = self.lattice.shear_factor
            scaling_factor = self.lattice.scaling_factor

            #Convert path to string
            save_path = save_path.__str__()

            #save channel/s for each timepoint. 
            #TODO: Check speed -> Channel and then timepoint or vice versa, which is faster?
            for time_point in progress(time_range):
                images_array=[] 
                for ch in progress(channel_range):
                    deskew_img=get_deskew_arr(self.aics, self.lattice.deskew_shape, self.lattice.deskew_vol_shape, time= time_point, channel=ch, scene=0, skew_dir=self.lattice.skew)
                    
                    #Perform deskewing on the skewed dask array 
                    deskew_full=deskew_zeiss(deskew_img,angle,shear_factor,scaling_factor,self.lattice.deskew_translate_y,reverse=False,dask=False)

                    #Crop the z slices to get only the deskewed array and not the empty area
                    deskew_final=deskew_full[self.lattice.deskew_z_start:self.lattice.deskew_z_end].astype('uint16')
                    images_array.append(deskew_final)
                images_array=np.array(images_array) #convert to array of arrays
                #images_array is in the format CZYX, but when saving for imagej, it should be TZCYXS; may need to add a flag for this
                # TODO: Add flag for saving in imagej format
                images_array=np.swapaxes(images_array,0,1)
                final_name=save_path+os.sep+"C"+str(time_point)+"T"+str(time_point)+"_"+self.save_name+".ome.tif"
                tifffile.imwrite(final_name,images_array,metadata={"axes":"ZCYX",'spacing': self.lattice.dy, 'unit': 'um',},dtype='uint16')
            print(time_start)
            print("Complete")
            #print(save_path)

@napari_hook_implementation
def napari_experimental_provide_dock_widget():
    # you can return either a single widget, or a sequence of widgets
    return [(LLSZWidget, {"name" : "LLSZ Widget"} )]


""""
Testing out UI only
Disable napari hook to just the UI

ui=LLSZWidget()
ui.show(run=True)
"""