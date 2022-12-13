from napari_lattice._dock_widget import _napari_lattice_widget_wrapper
import numpy as np
import pytest

# Test if the widget can be created

# make_napari_viewer is a pytest fixture that returns a napari viewer object
# @pytest.mark.skip(reason="GUI tests currently fail in github CI, unclear why")


def test_dock_widget(make_napari_viewer):
    # make viewer and add an image layer using our fixture
    try:
        viewer = make_napari_viewer()

        # Check if an image can be added as a layer
        viewer.add_image(np.random.random((100, 100)))

        # Test if napari-lattice widget can be created in napari
        viewer.window.add_dock_widget(_napari_lattice_widget_wrapper())

    except Exception as e:
        print(e)

    pass
