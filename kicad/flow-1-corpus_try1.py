import pcbnew
MM = 1_000_000
board = pcbnew.CreateEmptyBoard()
def seg(x1, y1, x2, y2, w=0.4):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetStart(pcbnew.VECTOR2I(int(x1*MM), int(y1*MM)))
    s.SetEnd(pcbnew.VECTOR2I(int(x2*MM), int(y2*MM)))
    s.SetWidth(int(w*MM))
    board.Add(s)
def circ(cx, cy, r, filled=False, lw=0.3):
    c = pcbnew.PCB_SHAPE(board)
    c.SetShape(pcbnew.SHAPE_T_CIRCLE)
    c.SetLayer(pcbnew.F_SilkS)
    c.SetCenter(pcbnew.VECTOR2I(int(cx*MM), int(cy*MM)))
    c.SetEnd(pcbnew.VECTOR2I(int((cx+r)*MM), int(cy*MM)))
    c.SetWidth(int(lw*MM))
    c.SetFilled(filled)
    board.Add(c)
def rect(x, y, w, h, lw=0.4):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetLayer(pcbnew.F_SilkS)
    s.SetStart(pcbnew.VECTOR2I(int(x*MM), int(y*MM)))
    s.SetEnd(pcbnew.VECTOR2I(int((x+w)*MM), int((y+h)*MM)))
    s.SetWidth(int(lw*MM))
    board.Add(s)
def text(x, y, value, size=2.5):
    tx = pcbnew.PCB_TEXT(board)
    tx.SetText(value)
    tx.SetLayer(pcbnew.F_SilkS)
    tx.SetPosition(pcbnew.VECTOR2I(int(x*MM), int(y*MM)))
    tx.SetTextSize(pcbnew.VECTOR2I(int(size*MM), int(size*MM)))
    tx.SetTextThickness(int(0.3*MM))
    board.Add(tx)

# Draw rectangles
rect(35, 5, 20, 8)   # START
rect(35, 18, 20, 8)  # VALIDATE
rect(35, 31, 20, 8)  # STORE
rect(35, 44, 20, 8)  # DONE

# Draw arrows (vertical)
seg(45, 13, 45, 18, 0.4)   # START to VALIDATE
seg(45, 26, 45, 31, 0.4)   # VALIDATE to STORE
seg(45, 39, 45, 44, 0.4)   # STORE to DONE

# Draw text labels
text(45, 9, "START", 2.5)
text(45, 22, "VALIDATE", 2.5)
text(45, 35, "STORE", 2.5)
text(45, 48, "DONE", 2.5)

pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\flow-1-corpus.kicad_pcb', board)
