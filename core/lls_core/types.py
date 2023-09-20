from typing import Union
from typing_extensions import TypeGuard, Any, TypeAlias
from dask.array.core import Array as DaskArray
# from numpy.typing import NDArray
from pyopencl.array import Array as OCLArray
import numpy as np
from numpy.typing import NDArray
from xarray import DataArray
from aicsimageio import AICSImage
from os import fspath, PathLike as OriginalPathLike

# This is a superset of os.PathLike
PathLike: TypeAlias = Union[str, bytes, OriginalPathLike]
def is_pathlike(x: Any) -> TypeGuard[PathLike]:
    return isinstance(x, (str, bytes, OriginalPathLike))

ArrayLike: TypeAlias = Union[DaskArray, NDArray, OCLArray, DataArray]

def is_arraylike(arr: Any) -> TypeGuard[ArrayLike]:
    return isinstance(arr, (DaskArray, np.ndarray, OCLArray, DataArray))

ImageLike: TypeAlias = Union[PathLike, AICSImage, ArrayLike]
def image_like_to_image(img: ImageLike) -> DataArray:
    """
    Converts an image in one of many formats to a DataArray
    """
    # First try treating it as a path
    try:
        img = AICSImage(fspath(img))
    except TypeError:
        pass
    if isinstance(img, AICSImage):
        return img.xarray_dask_data
    else:
        return DataArray(img)
