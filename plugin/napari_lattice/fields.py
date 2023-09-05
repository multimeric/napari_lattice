from pathlib import Path
from magicclass import FieldGroup, field, MagicTemplate
from magicclass.widgets import Widget, ComboBox, Label
from magicclass.fields import MagicField
from typing import Callable, List, Optional, Protocol, Tuple, TypeVar, Union, cast
from typing_extensions import Protocol, Self
from pydantic import BaseModel, ValidationError

from strenum import StrEnum
from lls_core import DeconvolutionChoice, SaveFileType, Log_Levels, DeskewDirection
from lls_core.lattice_data import CropParams, DeconvolutionParams, DefinedPixelSizes, LatticeData, OutputParams, DeskewParams
from napari.layers import Layer
from enum import Enum
import pyclesperanto_prototype as cle
from napari_workflows import Workflow, WorkflowManager
from napari.types import ImageData, ShapesData
from napari.utils import history
from abc import ABC
from qtpy.QtWidgets import QTabWidget

from napari_lattice.icons import RED, GREEN, GREY
from napari_lattice.reader import lattice_params_from_napari
from napari_lattice.utils import get_viewer

from napari.layers import Image

# FieldGroups that the users interact with to input data

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def _get_friendly_validations(model: FieldGroup) -> str:
    """
    Generates a LatticeData, but returns validation errors in a user friendly way
    """
    try:
        model._make_model()
        return ""
    except ValidationError as e:
        message = []
        # message = [f"<h2>{e.model}</h2>"]
        for error in e.errors():
            header = ", ".join([str(it).capitalize() for it in error['loc']])
            message.append(f"<li> <b>{header}</b> {error['msg']} </li>")
        joined = '\n'.join(message)
        return f"<ul>{joined}</ul>"
    except Exception as e:
        return str(e)


class WorkflowSource(StrEnum):
    ActiveWorkflow = "Active Workflow"
    CustomPath = "Custom Path"

class BackgroundSource(StrEnum):
    Auto = "Automatic"
    SecondLast = "Second Last"
    Custom = "Custom"


def enable_field(parent: MagicTemplate, field: MagicField, enabled: bool = True) -> None:
    """
    Enable the widget associated with a field

    Args:
        parent: The parent magicclass that contains the field
        field: The field to enable/disable
        enabled: If False, disable the field instead of enabling it
    """
    real_field = getattr(parent, field.name)
    if not isinstance(real_field, Widget):
        raise Exception("Define your fields with field() not vfield()!")
    real_field.visible = enabled
    real_field.enabled = enabled


EnabledHandlerType = TypeVar("EnabledHandlerType")
def enable_if(fields: List[MagicField]):
    """ 
    Makes an event handler that should be used via `fields_enabled.connect(make_enabled_handler())`.
    Args:
        condition: A function that takes an instance of the class and returns True if the fields should be enabled
        fields: A list of fields to be dynamically enabled or disabled
    Example:
        ::
            @some_field.connect
            @enable_if(
                [some_field]
            )
            def _enable_fields(self) -> bool:
                return some_field.value
    """
    # Ideally we could use subclassing to add both the vfield and this event handler, but there
    # seems to be a bug preventing this: https://github.com/hanjinliu/magic-class/issues/113.

    # Start by disabling all the fields

    def _decorator(fn: Callable[[EnabledHandlerType], bool])-> Callable[[EnabledHandlerType], None]:
        for field in fields:
            field.visible = False
            field.enabled = False

        def make_handler(fn: Callable) -> Callable[[EnabledHandlerType], None]:
            def handler(parent: EnabledHandlerType):
                enable = fn(parent)
                if enable:
                    logger.info(f"{parent.__class__} Activated")
                else:
                    logger.info(f"{parent.__class__} Deactivated")
                for field in fields:
                    enable_field(parent, field, enabled=enable)
            return handler

        return make_handler(fn)
    
    return _decorator


class LastDimensionOptions(Enum):
    XYZTC = "XYZTC"
    XYZCT = "XYZCT"
    Metadata = "Get from Metadata"

# class NapariFields(FieldGroup, ABC):
#     def __init__(self, layout: str = "vertical", labels: bool = False, name: str | None = None, **kwargs):
#         super().__init__(layout, labels, name, **kwargs)

class NapariFieldGroupCompatible(Protocol):
    errors: MagicField[Label]

class FieldGroupMeta(type(Protocol), type(FieldGroup)):
    pass

class NapariFieldGroup(NapariFieldGroupCompatible, Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self = cast(FieldGroup, self)
        self.changed.connect(self._validate)

    def _get_parent_tab_widget(self: Union[FieldGroup, Self]) -> QTabWidget:
        from qtpy.QtWidgets import QWidget
        parent = cast(QWidget, self.parent)
        return parent.parentWidget()

    def _get_tab_index(self: Union[FieldGroup, Self]) -> int:
        from magicclass.widgets import Container
        widget = cast(Container, self._widget)
        return self._get_parent_tab_widget().indexOf(widget._qwidget)

    def _set_valid(self, valid: bool):
        from qtpy.QtGui import QIcon
        tab_parent = self._get_parent_tab_widget()
        index = self._get_tab_index()
            
        if valid:
            tab_parent.setTabIcon(index, QIcon(GREEN))
        else:
            tab_parent.setTabIcon(index, QIcon(RED))

    def _validate(self: Union[FieldGroup, Self]):
        self.errors.value =  _get_friendly_validations(self)
        self._set_valid(False if self.errors.value else True)

    def _make_model(self) -> BaseModel:
        raise NotImplementedError()


class DeskewFields(FieldGroup, NapariFieldGroup, metaclass=FieldGroupMeta):

    def _get_dimension_options(self, _) -> List[str]:
        """
        Returns the list of dimension order options that might be possible for the current image stack
        """
        default = ["Get from Metadata"]
        try:
            merged = self._merge_layers()
        except Exception:
            return default
        ndims = len(merged._dims_order)
        if ndims == 3:
            return ["ZYX"] + default
        elif ndims == 4:
            return ["TZYX", "CZYX"] + default
        elif ndims == 5:
            return ["TCZYX", "CTZYX"] + default
        else:
            raise Exception("Only 3-5 dimensional arrays are supported")

    img_layer = field(List[Image], widget_type='Select').with_options(label = "Image Layer").with_choices(get_viewer().layers)
    pixel_sizes = field(Tuple[float, float, float]).with_options(
        value=(DefinedPixelSizes.get_default("X"), DefinedPixelSizes.get_default("Y"), DefinedPixelSizes.get_default("Z")),
        label="Pixel Sizes (XYZ)"
    )
    angle = field(LatticeData.get_default("angle")).with_options(value=LatticeData.get_default("angle"), label="Skew Angle")
    device = field(str).with_choices(cle.available_device_names()).with_options(label="Graphics Device")
    # merge_all_channels = field(False).with_options(label="Merge all Channels")
    dimension_order = field(str).with_options(value=LastDimensionOptions.Metadata.value).with_choices(_get_dimension_options).with_options(label="Dimension Order")
    skew_dir = field(DeskewDirection.Y).with_options(label = "Skew Direction")
    errors = field(Label).with_options(label="Errors")

    def _merge_layers(self) -> Image:
        """
        Returns a single image merged from all the selected layers
        """
        from napari.layers.utils.stack_utils import images_to_stack
        return images_to_stack(self.img_layer.value)

    def _make_model(self) -> DeskewParams:
        return DeskewParams(
            **lattice_params_from_napari(
                img=self._merge_layers(),
                last_dimension=self.dimension_order.value,
                physical_pixel_sizes=self.pixel_sizes.value,
            ),
            angle=self.angle.value,
            skew = self.skew_dir.value,
        )

class DeconvolutionFields(NapariFieldGroup, FieldGroup, metaclass=FieldGroupMeta):
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


    # @background.connect
    # def _show_custom_background(self):
    #     self.background_custom.visible = self.background == BackgroundSource.Custom
        
    @background.connect
    @enable_if(
        [background_custom]
    )
    def _enable_custom_background(self) -> bool:
        return self.background.value == BackgroundSource.Custom

    # @fields_enabled.connect
    # def _enable_fields(self) -> bool:
    #     self.decon_processing.visible = self.fields_enabled

    @fields_enabled.connect
    @enable_if(
        fields = [
            decon_processing,
            psf,
            psf_num_iter,
            background
        ]
    )
    def _enable_fields(self) -> bool:
        return self.fields_enabled.value

    def _make_model(self) -> Optional[DeconvolutionParams]:
        if not self.fields_enabled.value:
            return None
        return DeconvolutionParams(
            decon_processing=self.decon_processing.value,
            background=self.background.value
        )

class CroppingFields(FieldGroup, NapariFieldGroup, metaclass=FieldGroupMeta):
    """
    A counterpart to the CropParams Pydantic class
    """
    fields_enabled = field(False, label="Enabled")
    shapes= field(ShapesData, label = "ROIs")#Optional[Shapes] = None
    z_range = field(Tuple[int, int]).with_options(
        label = "Z Range",
        value = (0, 1),
        options = dict(
            min = 0,
            max = 1
        ),
    )
    errors = field(Label).with_options(label="Errors")

    @fields_enabled.connect
    @enable_if([shapes, z_range])
    def _enable_workflow(self) -> bool:
        return self.fields_enabled.value


    # roi_layer_list: ShapesData
    # @magicclass(visible=False)
    # class Fields(MagicTemplate):
    #     shapes= vfield(ShapesData, label = "ROIs")#Optional[Shapes] = None
    #     z_range = vfield(Tuple[int, int]).with_options(
    #         label = "Z Range",
    #         value = (0, 1),
    #         options = dict(
    #             min = 0,
    #             max = 1
    #         ),
    #     )
        # _shapes_layer: Optional[Shapes] = None

        # @set_design(font_size=10, text="Click to activate Cropping Layer", background_color="magenta")
        # @click(enables=["Import_ImageJ_ROI", "Crop_Preview"])
        # @set_design(text="New Cropping Layer")
        # def activate_cropping(self):
        #     self._shapes_layer = self.parent_viewer.add_shapes(shape_type='polygon', edge_width=1, edge_color='white',
        #                                                         face_color=[1, 1, 1, 0], name="Cropping BBOX layer")
        #     # TO select ROIs if needed
        #     self._shapes_layer.mode = "SELECT"
        #     self["activate_cropping"].text = "Cropping layer active"
        #     self["activate_cropping"].background_color = "green"

    def _make_model(self) -> Optional[CropParams]:
        return CropParams(
            z_start=self.z_range.value[0],
            z_end=self.z_range.value[1],
            roi_layer_list=self.shapes.value
    )

class WorkflowFields(FieldGroup, NapariFieldGroup, metaclass=FieldGroupMeta):
    """
    Handles the workflow related parameters
    """
    fields_enabled = field(False, label="Enabled")
    workflow_source = field(ComboBox).with_options(label = "Workflow Source").with_choices([it.value for it in WorkflowSource])
    workflow_path = field(Path).with_options(label = "Workflow Path", visible=False)
    errors = field(Label).with_options(label="Errors")

    @fields_enabled.connect
    @enable_if([workflow_source])
    def _enable_workflow(self) -> bool:
        return self.fields_enabled.value

    @fields_enabled.connect
    @enable_if([workflow_path])
    def _workflow_path(self) -> bool:
        return self.workflow_source.value == WorkflowSource.CustomPath

    def _make_model(self) -> Optional[Workflow]:
        if not self.fields_enabled.value:
            return None
        child = get_child(self, WorkflowFields)
        if child.workflow_source == WorkflowSource.ActiveWorkflow:
            return WorkflowManager.install(self.parent_viewer).workflow
        else:
            import_workflow_modules(child.workflow_path)
            return load_workflow(str(child.workflow_path))

# @magicclass(name="5. Output")
class OutputFields(FieldGroup, NapariFieldGroup, metaclass=FieldGroupMeta):
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


# @DeskewWidget.img_layer.connect
