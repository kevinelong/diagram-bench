import pcbnew

def main():
    # Create empty board
    board = pcbnew.CreateEmptyBoard()
    
    # Set board dimensions (0 to 90 mm horizontally, 0 to 60 mm vertically)
    board.SetWidth(pcbnew.FromMM(90))
    board.SetHeight(pcbnew.FromMM(60))
    
    # Create dark blue background (use a filled rectangle)
    bg_rect = pcbnew.PCB_SHAPE(board)
    bg_rect.SetShape(pcbnew.SHAPE_T_RECT)
    bg_rect.SetLayer(pcbnew.F_SilkS)
    bg_rect.SetStart(pcbnew.VECTOR2I(0, 0))
    bg_rect.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(90), pcbnew.FromMM(60)))
    bg_rect.SetWidth(0)  # No line width for filled rectangle
    
    # Add the background rectangle to the board
    board.Add(bg_rect)
    
    # Draw bracket outline (rectangle)
    bracket = pcbnew.PCB_SHAPE(board)
    bracket.SetShape(pcbnew.SHAPE_T_SEGMENT)
    bracket.SetLayer(pcbnew.F_SilkS)
    bracket.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(10)))
    bracket.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(10)))
    bracket.SetWidth(pcbnew.FromMM(0.15))
    board.Add(bracket)
    
    bracket = pcbnew.PCB_SHAPE(board)
    bracket.SetShape(pcbnew.SHAPE_T_SEGMENT)
    bracket.SetLayer(pcbnew.F_SilkS)
    bracket.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(10)))
    bracket.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(50)))
    bracket.SetWidth(pcbnew.FromMM(0.15))
    board.Add(bracket)
    
    bracket = pcbnew.PCB_SHAPE(board)
    bracket.SetShape(pcbnew.SHAPE_T_SEGMENT)
    bracket.SetLayer(pcbnew.F_SilkS)
    bracket.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(50)))
    bracket.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(50)))
    bracket.SetWidth(pcbnew.FromMM(0.15))
    board.Add(bracket)
    
    bracket = pcbnew.PCB_SHAPE(board)
    bracket.SetShape(pcbnew.SHAPE_T_SEGMENT)
    bracket.SetLayer(pcbnew.F_SilkS)
    bracket.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(50)))
    bracket.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(10)))
    bracket.SetWidth(pcbnew.FromMM(0.15))
    board.Add(bracket)
    
    # Draw mounting holes (circles)
    hole1 = pcbnew.PCB_SHAPE(board)
    hole1.SetShape(pcbnew.SHAPE_T_CIRCLE)
    hole1.SetLayer(pcbnew.F_SilkS)
    hole1.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(20), pcbnew.FromMM(20)))
    hole1.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(20), pcbnew.FromMM(20)))
    hole1.SetWidth(pcbnew.FromMM(3))
    board.Add(hole1)
    
    hole2 = pcbnew.PCB_SHAPE(board)
    hole2.SetShape(pcbnew.SHAPE_T_CIRCLE)
    hole2.SetLayer(pcbnew.F_SilkS)
    hole2.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(70), pcbnew.FromMM(40)))
    hole2.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(70), pcbnew.FromMM(40)))
    hole2.SetWidth(pcbnew.FromMM(3))
    board.Add(hole2)
    
    # Draw dimension line A
    dim_line_a = pcbnew.PCB_SHAPE(board)
    dim_line_a.SetShape(pcbnew.SHAPE_T_SEGMENT)
    dim_line_a.SetLayer(pcbnew.F_SilkS)
    dim_line_a.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(55)))
    dim_line_a.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(55)))
    dim_line_a.SetWidth(pcbnew.FromMM(0.15))
    board.Add(dim_line_a)
    
    # Draw arrows for dimension A
    arrow1 = pcbnew.PCB_SHAPE(board)
    arrow1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow1.SetLayer(pcbnew.F_SilkS)
    arrow1.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(53)))
    arrow1.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(57)))
    arrow1.SetWidth(pcbnew.FromMM(0.15))
    board.Add(arrow1)
    
    arrow2 = pcbnew.PCB_SHAPE(board)
    arrow2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow2.SetLayer(pcbnew.F_SilkS)
    arrow2.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(53)))
    arrow2.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(80), pcbnew.FromMM(57)))
    arrow2.SetWidth(pcbnew.FromMM(0.15))
    board.Add(arrow2)
    
    # Draw dimension line B
    dim_line_b = pcbnew.PCB_SHAPE(board)
    dim_line_b.SetShape(pcbnew.SHAPE_T_SEGMENT)
    dim_line_b.SetLayer(pcbnew.F_SilkS)
    dim_line_b.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(10)))
    dim_line_b.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(50)))
    dim_line_b.SetWidth(pcbnew.FromMM(0.15))
    board.Add(dim_line_b)
    
    # Draw arrows for dimension B
    arrow3 = pcbnew.PCB_SHAPE(board)
    arrow3.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow3.SetLayer(pcbnew.F_SilkS)
    arrow3.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(3), pcbnew.FromMM(10)))
    arrow3.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(7), pcbnew.FromMM(10)))
    arrow3.SetWidth(pcbnew.FromMM(0.15))
    board.Add(arrow3)
    
    arrow4 = pcbnew.PCB_SHAPE(board)
    arrow4.SetShape(pcbnew.SHAPE_T_SEGMENT)
    arrow4.SetLayer(pcbnew.F_SilkS)
    arrow4.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(3), pcbnew.FromMM(50)))
    arrow4.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(7), pcbnew.FromMM(50)))
    arrow4.SetWidth(pcbnew.FromMM(0.15))
    board.Add(arrow4)
    
    # Add text labels
    text_a = pcbnew.PCB_TEXT(board)
    text_a.SetText("A")
    text_a.SetLayer(pcbnew.F_SilkS)
    text_a.SetTextPosition(pcbnew.VECTOR2I(pcbnew.FromMM(45), pcbnew.FromMM(58)))
    text_a.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(2), pcbnew.FromMM(2)))
    text_a.SetTextThickness(pcbnew.FromMM(0.2))
    board.Add(text_a)
    
    text_b = pcbnew.PCB_TEXT(board)
    text_b.SetText("B")
    text_b.SetLayer(pcbnew.F_SilkS)
    text_b.SetTextPosition(pcbnew.VECTOR2I(pcbnew.FromMM(2), pcbnew.FromMM(30)))
    text_b.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(2), pcbnew.FromMM(2)))
    text_b.SetTextThickness(pcbnew.FromMM(0.2))
    board.Add(text_b)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\blueprint-1.kicad_pcb', board)

if __name__ == "__main__":
    main()
