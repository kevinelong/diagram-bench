import pcbnew

def main():
    # Create empty board
    board = pcbnew.CreateEmptyBoard()
    
    # Set board dimensions (0 to 90 mm horizontally, 0 to 60 mm vertically)
    board.SetWidth(pcbnew.FromMM(90))
    board.SetHeight(pcbnew.FromMM(60))
    
    # Create dark blue background (F_SilkS layer)
    # Draw a rectangle covering the entire board area
    background = pcbnew.PCB_SHAPE(board)
    background.SetShape(pcbnew.SHAPE_T_RECT)
    background.SetLayer(pcbnew.F_SilkS)
    background.SetStart(pcbnew.VECTOR2I(0, 0))
    background.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(90), pcbnew.FromMM(60)))
    background.SetWidth(100000)  # Small width to make it a filled rectangle
    
    # Add two circular mounting holes (centered in the bracket)
    # Hole 1
    hole1 = pcbnew.PCB_SHAPE(board)
    hole1.SetShape(pcbnew.SHAPE_T_CIRCLE)
    hole1.SetLayer(pcbnew.F_SilkS)
    hole1.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(20), pcbnew.FromMM(30)))
    hole1.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(20 + 5), pcbnew.FromMM(30)))
    hole1.SetWidth(pcbnew.FromMM(5))
    
    # Hole 2
    hole2 = pcbnew.PCB_SHAPE(board)
    hole2.SetShape(pcbnew.SHAPE_T_CIRCLE)
    hole2.SetLayer(pcbnew.F_SilkS)
    hole2.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(70), pcbnew.FromMM(30)))
    hole2.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(70 + 5), pcbnew.FromMM(30)))
    hole2.SetWidth(pcbnew.FromMM(5))
    
    # Draw dimension lines with arrows
    # Horizontal dimension line (A)
    dim_line_a = pcbnew.PCB_SHAPE(board)
    dim_line_a.SetShape(pcbnew.SHAPE_T_SEGMENT)
    dim_line_a.SetLayer(pcbnew.F_SilkS)
    dim_line_a.SetStart(pcbnew.VECTOR2I(0, pcbnew.FromMM(10)))
    dim_line_a.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(90), pcbnew.FromMM(10)))
    dim_line_a.SetWidth(100000)
    
    # Arrow for A
    arrow_a1 = pcbnew.PCB_SHAPE(board)
    arrow_a1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_a1.SetLayer(pcbnew.F_SilkS)
    arrow_a1.SetStart(pcbnew.VECTOR2I(0, pcbnew.FromMM(10)))
    arrow_a1.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(15)))
    arrow_a1.SetWidth(100000)
    
    arrow_a2 = pcbnew.PCB_SHAPE(board)
    arrow_a2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_a2.SetLayer(pcbnew.F_SilkS)
    arrow_a2.SetStart(pcbnew.VECTOR2I(0, pcbnew.FromMM(10)))
    arrow_a2.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(5)))
    arrow_a2.SetWidth(100000)
    
    # Arrow for A (end)
    arrow_a3 = pcbnew.PCB_SHAPE(board)
    arrow_a3.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_a3.SetLayer(pcbnew.F_SilkS)
    arrow_a3.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(90), pcbnew.FromMM(10)))
    arrow_a3.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(85), pcbnew.FromMM(15)))
    arrow_a3.SetWidth(100000)
    
    arrow_a4 = pcbnew.PCB_SHAPE(board)
    arrow_a4.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_a4.SetLayer(pcbnew.F_SilkS)
    arrow_a4.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(90), pcbnew.FromMM(10)))
    arrow_a4.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(85), pcbnew.FromMM(5)))
    arrow_a4.SetWidth(100000)
    
    # Vertical dimension line (B)
    dim_line_b = pcbnew.PCB_SHAPE(board)
    dim_line_b.SetShape(pcbnew.SHAPE_T_SEGMENT)
    dim_line_b.SetLayer(pcbnew.F_SilkS)
    dim_line_b.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), 0))
    dim_line_b.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(60)))
    dim_line_b.SetWidth(100000)
    
    # Arrow for B (top)
    arrow_b1 = pcbnew.PCB_SHAPE(board)
    arrow_b1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_b1.SetLayer(pcbnew.F_SilkS)
    arrow_b1.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), 0))
    arrow_b1.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(5)))
    arrow_b1.SetWidth(100000)
    
    arrow_b2 = pcbnew.PCB_SHAPE(board)
    arrow_b2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_b2.SetLayer(pcbnew.F_SilkS)
    arrow_b2.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), 0))
    arrow_b2.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(15), pcbnew.FromMM(5)))
    arrow_b2.SetWidth(100000)
    
    # Arrow for B (bottom)
    arrow_b3 = pcbnew.PCB_SHAPE(board)
    arrow_b3.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_b3.SetLayer(pcbnew.F_SilkS)
    arrow_b3.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(60)))
    arrow_b3.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(55)))
    arrow_b3.SetWidth(100000)
    
    arrow_b4 = pcbnew.PCB_SHAPE(board)
    arrow_b4.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow_b4.SetLayer(pcbnew.F_SilkS)
    arrow_b4.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(60)))
    arrow_b4.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(15), pcbnew.FromMM(55)))
    arrow_b4.SetWidth(100000)
    
    # Add text labels
    label_a = pcbnew.PCB_TEXT(board)
    label_a.SetText("A")
    label_a.SetLayer(pcbnew.F_SilkS)
    label_a.SetTextPosition(pcbnew.VECTOR2I(pcbnew.FromMM(45), pcbnew.FromMM(5)))
    label_a.SetTextSize(pcbnew.VECTOR2I(1000000, 1000000))
    label_a.SetTextThickness(100000)
    
    label_b = pcbnew.PCB_TEXT(board)
    label_b.SetText("B")
    label_b.SetLayer(pcbnew.F_SilkS)
    label_b.SetTextPosition(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(30)))
    label_b.SetTextSize(pcbnew.VECTOR2I(1000000, 1000000))
    label_b.SetTextThickness(100000)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\blueprint-1.kicad_pcb', board)

if __name__ == "__main__":
    main()
