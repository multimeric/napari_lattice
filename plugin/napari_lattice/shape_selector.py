from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator, Tuple, TYPE_CHECKING
from magicclass import field, magicclass
from magicgui.widgets import Select
from napari.layers import Shapes
from napari.components.layerlist import LayerList
from collections import defaultdict
from contextlib import contextmanager

from napari_lattice.utils import get_viewer

if TYPE_CHECKING:
    from napari.utils.events.event import Event
    from numpy.typing import NDArray

@dataclass(frozen=True, eq=True)
class Shape:
    """
    Holds data about a single shape within a Shapes layer
    """
    layer: Shapes
    index: int

    def __str__(self) -> str:
        return f"{self.layer.name}: Shape {self.index}"

    def get_array(self) -> NDArray:
        return self.layer.data[self.index]

@magicclass
class ShapeSelector:

    _blocked: bool

    def __init__(self, *args, **kwargs) -> None:
        # Needed to handle extra kwargs
        self._blocked = False

    @contextmanager
    def _block(self):
        """
        Context manager that prevents event handlers recursively calling each other.
        Yields a boolean which means functions should proceed if `True`, or return immediately if `False`
        """
        if self._blocked:
            yield False
        else:
            self._blocked = True
            yield True
            self._blocked = False

    def _get_shape_choices(self, widget: Select | None = None) -> Iterator[Tuple[str, Shape]]:
        """
        Returns the choices to use for the Select box
        """
        viewer = get_viewer()
        for layer in viewer.layers:
            if isinstance(layer, Shapes):
                for index in layer.features.index:
                    result = Shape(layer=layer, index=index)
                    yield str(result), result

    def _on_selection_change(self, event: Event) -> None:
        """
        Triggered when the user clicks on one or more shapes.
        The widget is then updated to synchronise
        """
        # Prevent recursion
        with self._block() as execute:
            if not execute:
                return

            source: Shapes = event.source
            selection: list[Shape] = []
            for index in source.selected_data:
                selection.append(Shape(layer=source, index=index))
            self.shapes.value = selection

    def _connect_shapes(self, shapes: Shapes) -> None:
        """
        Called on a newly discovered `Shapes` layer.
        Listens to events on that layer that we are interested in.
        """
        shapes.events.data.connect(self._on_shape_change)
        # There is no shape selection event. This is the closest thing.
        # See: https://github.com/napari/napari/issues/6886
        shapes.events.highlight.connect(self._on_selection_change)

    def _on_shape_change(self, event: Event) -> None:
        """
        Triggered whenever a shape layer changes.
        Resets the select box options
        """
        if isinstance(event.source, Shapes):
            self.shapes.reset_choices()

    def _on_layer_add(self, event: Event) -> None:
        """
        Triggered whenever a new layer is inserted.
        Ensures we listen for shape changes to that new layer
        """
        if isinstance(event.source, LayerList):
            for layer in event.source:
                if isinstance(layer, Shapes):
                    self._connect_shapes(layer)
                
    def __post_init__(self) -> None:
        """
        Whenever a new layer is inserted
        """
        viewer = get_viewer()

        # Listen for new layers
        viewer.layers.events.inserted.connect(self._on_layer_add)

        # Watch current layers
        for layer in viewer.layers:
            if isinstance(layer, Shapes):
                self._connect_shapes(layer)

    shapes = field(Select, options={"choices": _get_shape_choices})

    # values is a list[Shape], but if we use the correct annotation it breaks magicclass
    @shapes.connect
    def _widget_changed(self, values: list) -> None:
        """
        Triggered when the plugin widget is changed.
        We then synchronise the Napari shape selection with it.
        """
        with self._block() as execute:
            if not execute:
                return
            layers: dict[Shapes, list[int]] = {layer: [] for layer in get_viewer().layers if isinstance(layer, Shapes)}
            value: Shape

            # Find the current selection for each layer
            for value in values:
                layers[value.layer].append(value.index)

            # For each layer, set the appropriate selection (this can't be done incrementally)
            for layer, shapes in layers.items():
                layer.selected_data = shapes

            # Re-calculate the selections for all Shapes layers (since some have been deselected)
            for layer in layers.keys():
                    layer.refresh()
