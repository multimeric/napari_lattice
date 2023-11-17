from typing import Iterable, List, Tuple, Any
from pydantic import Field, NonNegativeInt, validator
from lls_core.models.utils import FieldAccessMixin
from lls_core.cropping import Roi

class CropParams(FieldAccessMixin):
    """
    Parameters for the optional cropping step
    """
    roi_list: List[Roi] = Field(
        description="List of regions of interest, each of which must be an NxD array, where N is the number of vertices and D the coordinates of each vertex.",
        default = []
    )
    roi_subset: List[int] = Field(
        description="A subset of all the ROIs to process",
        default=None
    )
    z_range: Tuple[NonNegativeInt, NonNegativeInt] = Field(
        default=None,
        description="The range of Z slices to take. All Z slices before the first index or after the last index will be cropped out."
    )

    @property
    def selected_rois(self) -> Iterable[Roi]:
        "Returns the relevant ROIs that should be processed"
        for i in self.roi_subset:
            yield self.roi_list[i]

    @validator("roi_list", pre=True)
    def read_roi(cls, v: Any) -> List[Roi]:
        from lls_core.types import is_pathlike
        from lls_core.cropping import read_imagej_roi
        from numpy import ndarray
        # Allow a single path
        if is_pathlike(v):
            v = [v]

        rois: List[Roi] = []
        for item in v:
            if is_pathlike(item):
                rois += read_imagej_roi(item)
            elif isinstance(item, ndarray):
                rois.append(Roi.from_array(item))
            elif isinstance(item, Roi):
                rois.append(item)
            else:
                # Try converting an iterable to ROI
                try:
                    rois.append(Roi(*item))
                except:
                    raise ValueError(f"{item} cannot be intepreted as an ROI")

        return rois

    @validator("roi_subset", pre=True, always=True)
    def default_roi_range(cls, v: Any, values: dict):
        # If the roi range isn't provided, assume all rois should be processed
        if v is None:
            return list(range(len(values["roi_list"])))
        return v
