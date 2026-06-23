import math
from ToolsPage import Plugin
from CNC import Block, CNC

try:
    _
except NameError:
    def _(s): return s

class Tool(Plugin):
    __doc__ = _("Mirror selected G-code blocks around their collective selection center or document center")

    def __init__(self, master):
        Plugin.__init__(self, master, "Mirror")
        self.icon = "mirror"
        self.group = "CAM"
        self.variables = [
            ("axis", "X,Y", "X", _("Axis"), _("Axis to flip across (X or Y)")),
            ("doc_center", "bool", 0, _("Use Doc Center"), _("Check for doc center, uncheck for collective selection center"))
        ]
        self.buttons.append("exe")

    @staticmethod
    def _get_margins(gcode, items):
        # Get bounding box of document
        minx, miny, maxx, maxy = 0, 0, 0, 0 # BUG!
        for bid, lid in items:
            paths = gcode.toPath(bid)
            for path in paths:
                minx2, miny2, maxx2, maxy2 = path.bbox()
                minx, miny, maxx, maxy = (
                    min(minx, minx2),
                    min(miny, miny2),
                    max(maxx, maxx2),
                    max(maxy, maxy2),
                )
        return minx, miny, maxx, maxy

    @staticmethod
    def _get_margins2(gcode, items=None):
        # Get bounding box of document
        minx, miny, maxx, maxy = float('inf'), float('inf'), float('-inf'), float('-inf')
        blocks = gcode.blocks
        if items is not None:
            blocks = [gcode.blocks[bid] for bid in set([bid for bid, _ in items])]
        for block in blocks:
            for line in block:
                cmds = CNC.parseLine(line.upper())
                if cmds is None:
                    continue
                for cmd in cmds:
                    try:
                        if cmd[0] in "XI":
                            val = float(cmd[1:]) * gcode.cnc.unit
                            minx = min(minx, val)
                            maxx = max(maxx, val)
                        elif cmd[0] in "YJ":
                            val = float(cmd[1:]) * gcode.cnc.unit
                            miny = min(miny, val)
                            maxy = max(maxy, val)
                    except Exception:
                        continue
        return (
            minx if not math.isinf(minx) else 0,
            miny if not math.isinf(miny) else 0,
            maxx if not math.isinf(maxx) else 0,
            maxy if not math.isinf(maxy) else 0
        )

    def execute(self, app):
        app.setStatus(_("Mirror: Extracting coordinates..."))

        axis = self["axis"]
        use_doc_center = int(self["doc_center"])

        blocks_sel = app.editor.getCleanSelection()
        if not blocks_sel:
            app.setStatus(_("Mirror: ERROR! No blocks selected."))
            return

        if use_doc_center:
            if not CNC.isMarginValid():
                app.setStatus("Mirror: ERROR! GCode margin invalid.")
            xmin, ymin, xmax, ymax = (
                CNC.vars["xmin"], CNC.vars["ymin"],
                CNC.vars["xmax"], CNC.vars["ymax"]
            )
        else:
            xmin, ymin, xmax, ymax = self._get_margins2(app.gcode, blocks_sel)
        cx = (xmin + xmax)# / 2.0
        cy = (ymin + ymax)# / 2.0

        if axis == "X":
            mod_args = ["XI", cx]
        elif axis == "Y":
            mod_args = ["YJ", cy]
        else:
            app.setStatus(_("Mirror: ERROR! Unsupported axis."))
            return

        processed_count = 0
        def mod_func(new, old, relative, *args):
            A, C = args
            changed = False
            for axis in A:
                if axis in new:
                    new[axis] = C - new[axis]
                    nonlocal processed_count
                    processed_count += 1
                    changed = True
            if app.gcode.cnc.gcode in (2, 3):  # Change  2<->3
                app.gcode.cnc.gcode = 5 - app.gcode.cnc.gcode
                changed = True
            return changed
        app.gcode.modify(blocks_sel, mod_func, None, *mod_args)
        if processed_count > 0:
            app.refresh()
        app.setStatus(_("Mirror: done ({} blocks mirrored around {},{})").format(processed_count, xmin, xmax))
