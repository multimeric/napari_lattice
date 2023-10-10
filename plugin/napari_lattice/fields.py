# FieldGroups that the users interact with to input data

import logging
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple, TypeVar, cast

import pyclesperanto_prototype as cle
from lls_core import (
    DeconvolutionChoice,
    DeskewDirection,
    Log_Levels,
)
from lls_core.models import (
    CropParams,
    DeconvolutionParams,
    DeskewParams,
    LatticeData,
    OutputParams,
)
from lls_core.models.deskew import DefinedPixelSizes
from lls_core.models.output import SaveFileType
from lls_core.workflow import workflow_from_path
from magicclass import FieldGroup, MagicTemplate, field, magicclass, set_design
from magicclass.fields import MagicField
from magicclass.widgets import ComboBox, Label, Widget
from napari.layers import Image, Shapes
from napari.types import ShapesData
from napari.utils import history
from napari_lattice.icons import GREEN, GREY, RED
from napari_lattice.reader import NapariImageParams, lattice_params_from_napari
from napari_lattice.utils import get_layers
from napari_workflows import Workflow, WorkflowManager
from qtpy.QtWidgets import QTabWidget
from strenum import StrEnum

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def exception_to_html(e: BaseException) -> str:
    """
    Converts an exception to HTML for reporting back to the user
    """
    from pydantic import ValidationError
    if isinstance(e, ValidationError):
        message = []
        for error in e.errors():
            header = ", ".join([str(it).capitalize() for it in error['loc']])
            message.append(f"<li> <b>{header}</b> {error['msg']} </li>")
        joined = '\n'.join(message)
        return f"<ul>{joined}</ul>"
    else:
        return str(e)

def get_friendly_validations(model: FieldGroup) -> str:
    """
    Generates a BaseModel, but returns validation errors in a user friendly way
    """
    try:
        model._make_model()
        return ""
    except BaseException as e:
        return exception_to_html(e)

class PixelSizeSource(StrEnum):
    Metadata = "Image Metadata"
    Manual = "Manual"

class WorkflowSource(StrEnum):
    ActiveWorkflow = "Active Workflow"
    CustomPath = "Custom Path"

class BackgroundSource(StrEnum):
    Auto = "Automatic"
    SecondLast = "Second Last"
    Custom = "Custom"


def enable_field(field: MagicField, enabled: bool = True) -> None:
    """
    Enable the widget associated with a field

    Args:
        field: The field to enable/disable
        enabled: If False, disable the field instead of enabling it
    """
    for real_field in field._guis.values():
        if not isinstance(real_field, Widget):
            raise Exception("Define your fields with field() not vfield()!")
        try:
            real_field.visible = enabled
            real_field.enabled = enabled
        except RuntimeError:
            pass


FieldValueType = TypeVar("FieldValueType")
SelfType = TypeVar("SelfType")
def enable_if(fields: List[MagicField]):
    """ 
    Makes an event handler that dynamically disables and enables a set of fields based on a criteria
    Args:
        condition: A function that takes an instance of the class and returns True if the fields should be enabled
        fields: A list of fields to be dynamically enabled or disabled
    Example:
        ::
            @some_field.connect
            @enable_if(
                [some_field]
            )
            def _enable_fields(self, value) -> bool:
                return value
    """
    # Ideally we could use subclassing to add both the vfield and this event handler, but there
    # seems to be a bug preventing this: https://github.com/hanjinliu/magic-class/issues/113.

    # Start by disabling all the fields

    def _decorator(fn: Callable[[SelfType, FieldValueType], bool])-> Callable[[SelfType, FieldValueType], None]:
        for field in fields:
            field.enabled = False
            field.visible = False

        def make_handler(fn: Callable[[SelfType, FieldValueType], bool]) -> Callable[[SelfType, FieldValueType], None]:
            def handler(self: Any, value: Any):
                enable = fn(self, value)
                for field in fields:
                    if enable:
                        logger.info(f"{field.name} Activated")
                    else:
                        logger.info(f"{field.name} Deactivated")
                    enable_field(field, enabled=enable)
            return handler

        return make_handler(fn)
    
    return _decorator

class StackAlong(StrEnum):
    CHANNEL = "Channel"
    TIME = "Time"

class NapariFieldGroup:
    def __post_init__(self):
        self = cast(FieldGroup, self)
        self.changed.connect(self._validate, unique=False)

        # Style the error label. 
        # We have to check this is a QLabel because in theory this might run in a non-QT backend
        errors = self.errors.native
        from qtpy.QtWidgets import QLabel
        if isinstance(errors, QLabel):
            errors.setStyleSheet("color: red;")
            errors.setWordWrap(True)

        from qtpy.QtCore import Qt
        self._widget._layout.setAlignment(Qt.AlignTop)

    def _get_parent_tab_widget(self: Any) -> QTabWidget:
        return self.parent.parentWidget()

    def _get_tab_index(self: Any) -> int:
        return self._get_parent_tab_widget().indexOf(self._widget._qwidget)

    def _set_valid(self: Any, valid: bool):
        from qtpy.QtGui import QIcon
        from importlib_resources import as_file
        tab_parent = self._get_parent_tab_widget()
        index = self._get_tab_index()
            
        if hasattr(self, "fields_enabled") and not self.fields_enabled.value:
            # Special case for "diabled" sections
            icon = GREY
        elif valid:
            icon = GREEN
        else:
            icon = RED

        with as_file(icon) as path:
            tab_parent.setTabIcon(index, QIcon(str(path)))

    def reset_choices(self: Any):
        # This is used to prevent validation from re-running when a napari layer is added or removed
        from magicgui.widgets import Container
        with self.changed.blocked():
            super(Container, self).reset_choices()

    def _validate(self: Any):
        self.errors.value = get_friendly_validations(self)
        valid = not bool(self.errors.value)
        self.errors.visible = not valid
        self._set_valid(valid)

    def _make_model(self):
        raise NotImplementedError()

class DeskewKwargs(NapariImageParams):
    angle: float
    skew: DeskewDirection

@magicclass
class DeskewFields(NapariFieldGroup):

    def _get_dimension_options(self, _) -> List[str]:
        """
        Returns the list of dimension order options that might be possible for the current image stack
        """
        default = ["Get from Metadata"]
        ndims = max([len(layer.data.shape)  for layer in self.img_layer.value], default=None)
        if ndims is None:
            return default
        elif ndims == 3:
            return ["ZYX"] + default
        elif ndims == 4:
            return ["TZYX", "CZYX"] + default
        elif ndims == 5:
            return ["TCZYX", "CTZYX"] + default
        else:
            raise Exception("Only 3-5 dimensional arrays are supported")

    img_layer = field(List[Image], widget_type='Select').with_options(
        label="Image Layer(s) to Deskew",
        tooltip="All the image layers you select will be stacked into one image and then deskewed. To select multiple layers, hold Command (MacOS) or Control (Windows, Linux)."
    ).with_choices(lambda _x, _y: get_layers(Image))
    stack_along = field(
        str,
    ).with_choices(
        [it.value for it in StackAlong]
    ).with_options(
        label="Stack Along",
        tooltip="The direction along which to stack multiple selected layers.",
        value=StackAlong.CHANNEL
    )
    pixel_sizes_source = field(PixelSizeSource.Metadata, widget_type="RadioButtons").with_options(label="Pixel Size Source", orientation="horizontal").with_choices([it.value for it in PixelSizeSource])
    pixel_sizes = field(Tuple[float, float, float]).with_options(
        label="Pixel Sizes: XYZ (µm)",
        tooltip="The size of each pixel in microns. The first field selects the X pixel size, then Y, then Z."
    )
    angle = field(LatticeData.get_default("angle")).with_options(
        value=LatticeData.get_default("angle"),
        label="Skew Angle (°)",
        tooltip="The angle to deskew the image, in degrees"
    )
    device = field(str).with_choices(cle.available_device_names()).with_options(
        label="Graphics Device",
        tooltip="The GPU that will be used to perform the processing"
    )
    # merge_all_channels = field(False).with_options(label="Merge all Channels")
    dimension_order = field(
        str
    ).with_choices(
        _get_dimension_options
    ).with_options(
        label="Dimension Order",
        tooltip="Specifies the order of dimensions in the input images. For example, if your image is a 4D array with multiple channels along the first axis, you will specify CZYX.",
        value="Get from Metadata"
    )
    skew_dir = field(DeskewDirection.Y, widget_type="RadioButtons").with_options(
        label="Skew Direction",
        tooltip="The axis along which to deskew",
        orientation="horizontal"
    )
    errors = field(Label).with_options(label="Errors")

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        from magicgui.widgets import TupleEdit
        from qtpy.QtWidgets import QDoubleSpinBox

        # Enormous hack to set the precision
        # A better method has been requested here: https://github.com/pyapp-kit/magicgui/issues/581#issuecomment-1709467219
        if isinstance(self.pixel_sizes, TupleEdit):
            for subwidget in self.pixel_sizes._list:
                if isinstance(subwidget.native, QDoubleSpinBox):
                    subwidget.native.setDecimals(10)
                    # We have to re-set the default value after changing the precision, because it's already been rounded up
                    # Also, we have to block emitting the changed signal at construction time
                    with self.changed.blocked():
                        self.pixel_sizes.value = (
                            DefinedPixelSizes.get_default("X"),
                            DefinedPixelSizes.get_default("Y"),
                            DefinedPixelSizes.get_default("Z")
                        )

    @img_layer.connect
    def _img_changed(self) -> None:
        # Recalculate the dimension options whenever the image changes
        self.dimension_order.reset_choices()

    @pixel_sizes_source.connect
    @enable_if([pixel_sizes])
    def _hide_pixel_sizes(self, pixel_sizes_source: str):
        # Hide the "Pixel Sizes" option unless the user specifies manual pixel size source
        return pixel_sizes_source == PixelSizeSource.Manual

    @img_layer.connect
    @enable_if([stack_along])
    def _hide_stack_along(self, img_layer: List[Image]):
        # Hide the "Stack Along" option if we only have one image
        return len(img_layer) > 1

    def _get_kwargs(self) -> DeskewKwargs:
        """
        Returns the LatticeData fields that the Deskew tab can provide
        """
        from aicsimageio.types import PhysicalPixelSizes
        DeskewParams.update_forward_refs()
        params = lattice_params_from_napari(
                imgs=self.img_layer.value,
                dimension_order=None if self.dimension_order.value == "Get from Metadata" else self.dimension_order.value,
                physical_pixel_sizes= None if self.pixel_sizes_source.value == PixelSizeSource.Metadata else PhysicalPixelSizes(
                    X = self.pixel_sizes.value[0],
                    Y = self.pixel_sizes.value[1],
                    Z = self.pixel_sizes.value[2]
                ),
                stack_along="C" if self.stack_along.value == StackAlong.CHANNEL else "T"
            )
        return DeskewKwargs(
            **params,
            angle=self.angle.value,
            skew = self.skew_dir.value,
        )

    def _make_model(self) -> DeskewParams:
        kwargs = self._get_kwargs()
        return DeskewParams(
            input_image=kwargs["data"],
            physical_pixel_sizes=kwargs["physical_pixel_sizes"],
            angle=kwargs["angle"],
            skew = kwargs["skew"]
        )

@magicclass
class DeconvolutionFields(NapariFieldGroup):
    """
    A counterpart to the DeconvolutionParams Pydantic class
    """
    fields_enabled = field(False, label="Enabled")
    decon_processing = field(DeconvolutionChoice, label="Processing Algorithm")
    psf = field(List[Path], label = "PSFs")
    psf_num_iter = field(int, label = "Number of Iterations")
    background = field(ComboBox).with_choices(
        [it.value for it in BackgroundSource]
    ).with_options(label="Background")
    background_custom = field(float).with_options(
        visible=False,
        label="Custom Background"
    )
    errors = field(Label).with_options(label="Errors")

    @background.connect
    @enable_if(
        [background_custom]
    )
    def _enable_custom_background(self, background: str) -> bool:
        return background == BackgroundSource.Custom

    @fields_enabled.connect
    @enable_if(
        fields = [
            decon_processing,
            psf,
            psf_num_iter,
            background
        ]
    )
    def _enable_fields(self, enabled: bool) -> bool:
        return enabled

    def _make_model(self) -> Optional[DeconvolutionParams]:
        if not self.fields_enabled.value:
            return None
        if self.background.value == BackgroundSource.Custom:
            background = self.background_custom.value
        elif self.background.value == BackgroundSource.Auto:
            background = "auto"
        else:
            background = "second_last"
        return DeconvolutionParams(
            decon_processing=self.decon_processing.value,
            background=background,
            psf=self.psf.value,
            psf_num_iter=self.psf_num_iter.value
        )

@magicclass
class CroppingFields(MagicTemplate, NapariFieldGroup):
    """
    A counterpart to the CropParams Pydantic class
    """
    fields_enabled = field(False, label="Enabled")
    shapes= field(List[Shapes], widget_type="Select", label = "ROI Shape Layers").with_options(choices=lambda _x, _y: get_layers(Shapes))
    z_range = field(Tuple[int, int]).with_options(
        label = "Z Range",
        value = (0, 1),
        options = dict(
            min = 0,
            max = 1
        ),
    )
    errors = field(Label).with_options(label="Errors")

    def _get_deskew(self) -> DeskewParams:
        "Returns the DeskewParams from the other tab"
        from napari_lattice.dock_widget import LLSZWidget
        parent = self.find_ancestor(LLSZWidget)
        return parent.LlszMenu.WidgetContainer.deskew_fields._make_model()

    @set_design(text="Import ROI")
    def import_roi(self, path: Path):
        from lls_core.cropping import read_imagej_roi
        from napari_lattice.utils import get_viewer
        import numpy as np
        roi_list = read_imagej_roi(path)
        # convert to canvas coordinates
        roi_list = (np.array(roi_list) * self._get_deskew().dy).tolist()
        viewer = get_viewer()
        viewer.add_shapes(roi_list, shape_type='polygon', edge_width=1, edge_color='yellow', face_color=[1, 1, 1, 0])

    @set_design(text="New Crop")
    def new_crop_layer(self):
        from napari_lattice.utils import get_viewer
        shapes = get_viewer().add_shapes()
        shapes.mode = "ADD_RECTANGLE"
        shapes.name = "Napari Lattice Crop"

    @fields_enabled.connect
    @enable_if([shapes, z_range])
    def _enable_crop(self, enabled: bool) -> bool:
        return enabled

    def _make_model(self) -> Optional[CropParams]:
        import numpy as np
        if self.fields_enabled.value:
            return CropParams(
                z_range=self.z_range.value,
                roi_list=ShapesData([np.array(shape.data) / self._get_deskew().dy  for shape in self.shapes.value])
            )
        return None

@magicclass
class WorkflowFields(NapariFieldGroup):
    """
    Handles the workflow related parameters
    """
    fields_enabled = field(False, label="Enabled")
    workflow_source = field(ComboBox).with_options(label = "Workflow Source").with_choices([it.value for it in WorkflowSource])
    workflow_path = field(Path).with_options(label = "Workflow Path", visible=False)
    errors = field(Label).with_options(label="Errors")

    @fields_enabled.connect
    @enable_if([workflow_source])
    def _enable_workflow(self, enabled: bool) -> bool:
        return enabled

    @workflow_source.connect
    @enable_if([workflow_path])
    def _workflow_path(self, workflow_source: WorkflowSource) -> bool:
        return workflow_source == WorkflowSource.CustomPath

    def _make_model(self) -> Optional[Workflow]:
        if not self.fields_enabled.value:
            return None
        if self.workflow_source.value == WorkflowSource.ActiveWorkflow:
            return WorkflowManager.install(self.parent_viewer).workflow
        else:
            return workflow_from_path(self.workflow_path.value)

@magicclass
class OutputFields(NapariFieldGroup):
    set_logging = field(Log_Levels.INFO).with_options(label="Logging Level")
    time_range = field(Tuple[int, int]).with_options(
        label="Time Export Range",
        value=(0, 1),
        options = dict(
            min=0,
            max=100,
        )
    )
    channel_range = field(Tuple[int, int]).with_options(
        label="Channel Range",
        value=(0, 1),
        options = dict(
            min=0,
            max=100,
        )
    )
    save_type = field(SaveFileType).with_options(
        label = "Save Format"
    )
    save_path = field(Path).with_options(
        label = "Save Directory",
        value = Path(history.get_save_history()[0])
    )
    save_name = field(str).with_options(
        label = "Save Prefix"
    )
    errors = field(Label).with_options(label="Errors")

    def _make_model(self) -> OutputParams:
        return OutputParams(
            channel_range=range(self.channel_range.value[0], self.channel_range.value[1]),
            time_range=range(self.time_range.value[0], self.time_range.value[1]),
            save_dir=self.save_path.value,
            save_name=self.save_name.value,
            save_type=self.save_type.value
        )