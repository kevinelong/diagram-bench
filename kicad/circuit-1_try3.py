import pcbnew

def create_rc_filter_board():
    # Create empty board
    board = pcbnew.CreateEmptyBoard()
    
    # Set board dimensions (0 to 90 mm horizontally, 0 to 60 mm vertically)
    board.SetWidth(pcbnew.FromMM(90))
    board.SetHeight(pcbnew.FromMM(60))
    
    # Define coordinates in nanometres
    mm_to_nm = 1000000
    
    # Input terminal (left side)
    input_start = pcbnew.VECTOR2I(int(5 * mm_to_nm), int(30 * mm_to_nm))  # Start at (5mm, 30mm)
    input_end = pcbnew.VECTOR2I(int(15 * mm_to_nm), int(30 * mm_to_nm))   # End at (15mm, 30mm)
    
    # Create input line
    input_line = pcbnew.PCB_SHAPE(board)
    input_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    input_line.SetLayer(pcbnew.F_SilkS)
    input_line.SetStart(input_start)
    input_line.SetEnd(input_end)
    input_line.SetWidth(int(0.1 * mm_to_nm))
    board.Add(input_line)
    
    # Resistor symbol (R1)
    # Horizontal resistor line
    res_start = pcbnew.VECTOR2I(int(15 * mm_to_nm), int(30 * mm_to_nm))  # Start at (15mm, 30mm)
    res_end = pcbnew.VECTOR2I(int(35 * mm_to_nm), int(30 * mm_to_nm))   # End at (35mm, 30mm)
    
    res_line = pcbnew.PCB_SHAPE(board)
    res_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    res_line.SetLayer(pcbnew.F_SilkS)
    res_line.SetStart(res_start)
    res_line.SetEnd(res_end)
    res_line.SetWidth(int(0.1 * mm_to_nm))
    board.Add(res_line)
    
    # Vertical lines for resistor symbol (zigzag pattern)
    zigzag_height = int(5 * mm_to_nm)
    zigzag_width = int(3 * mm_to_nm)
    
    # First vertical line
    vert1_start = pcbnew.VECTOR2I(int(20 * mm_to_nm), int(30 * mm_to_nm))
    vert1_end = pcbnew.VECTOR2I(int(20 * mm_to_nm), int(35 * mm_to_nm))
    
    vert1 = pcbnew.PCB_SHAPE(board)
    vert1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    vert1.SetLayer(pcbnew.F_SilkS)
    vert1.SetStart(vert1_start)
    vert1.SetEnd(vert1_end)
    vert1.SetWidth(int(0.1 * mm_to_nm))
    board.Add(vert1)
    
    # Second vertical line
    vert2_start = pcbnew.VECTOR2I(int(23 * mm_to_nm), int(35 * mm_to_nm))
    vert2_end = pcbnew.VECTOR2I(int(23 * mm_to_nm), int(30 * mm_to_nm))
    
    vert2 = pcbnew.PCB_SHAPE(board)
    vert2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    vert2.SetLayer(pcbnew.F_SilkS)
    vert2.SetStart(vert2_start)
    vert2.SetEnd(vert2_end)
    vert2.SetWidth(int(0.1 * mm_to_nm))
    board.Add(vert2)
    
    # Third vertical line
    vert3_start = pcbnew.VECTOR2I(int(26 * mm_to_nm), int(30 * mm_to_nm))
    vert3_end = pcbnew.VECTOR2I(int(26 * mm_to_nm), int(35 * mm_to_nm))
    
    vert3 = pcbnew.PCB_SHAPE(board)
    vert3.SetShape(pcbnew.SHAPE_T_SEGMENT)
    vert3.SetLayer(pcbnew.F_SilkS)
    vert3.SetStart(vert3_start)
    vert3.SetEnd(vert3_end)
    vert3.SetWidth(int(0.1 * mm_to_nm))
    board.Add(vert3)
    
    # Fourth vertical line
    vert4_start = pcbnew.VECTOR2I(int(29 * mm_to_nm), int(35 * mm_to_nm))
    vert4_end = pcbnew.VECTOR2I(int(29 * mm_to_nm), int(30 * mm_to_nm))
    
    vert4 = pcbnew.PCB_SHAPE(board)
    vert4.SetShape(pcbnew.SHAPE_T_SEGMENT)
    vert4.SetLayer(pcbnew.F_SilkS)
    vert4.SetStart(vert4_start)
    vert4.SetEnd(vert4_end)
    vert4.SetWidth(int(0.1 * mm_to_nm))
    board.Add(vert4)
    
    # Capacitor symbol (C1)
    cap_horiz_start = pcbnew.VECTOR2I(int(35 * mm_to_nm), int(30 * mm_to_nm))  # Start at (35mm, 30mm)
    cap_horiz_end = pcbnew.VECTOR2I(int(55 * mm_to_nm), int(30 * mm_to_nm))   # End at (55mm, 30mm)
    
    cap_line1 = pcbnew.PCB_SHAPE(board)
    cap_line1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    cap_line1.SetLayer(pcbnew.F_SilkS)
    cap_line1.SetStart(cap_horiz_start)
    cap_line1.SetEnd(cap_horiz_end)
    cap_line1.SetWidth(int(0.1 * mm_to_nm))
    board.Add(cap_line1)
    
    # Vertical lines for capacitor
    cap_vert1_start = pcbnew.VECTOR2I(int(45 * mm_to_nm), int(30 * mm_to_nm))
    cap_vert1_end = pcbnew.VECTOR2I(int(45 * mm_to_nm), int(20 * mm_to_nm))
    
    cap_vert1 = pcbnew.PCB_SHAPE(board)
    cap_vert1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    cap_vert1.SetLayer(pcbnew.F_SilkS)
    cap_vert1.SetStart(cap_vert1_start)
    cap_vert1.SetEnd(cap_vert1_end)
    cap_vert1.SetWidth(int(0.1 * mm_to_nm))
    board.Add(cap_vert1)
    
    # Second vertical line for capacitor
    cap_vert2_start = pcbnew.VECTOR2I(int(50 * mm_to_nm), int(30 * mm_to_nm))
    cap_vert2_end = pcbnew.VECTOR2I(int(50 * mm_to_nm), int(20 * mm_to_nm))
    
    cap_vert2 = pcbnew.PCB_SHAPE(board)
    cap_vert2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    cap_vert2.SetLayer(pcbnew.F_SilkS)
    cap_vert2.SetStart(cap_vert2_start)
    cap_vert2.SetEnd(cap_vert2_end)
    cap_vert2.SetWidth(int(0.1 * mm_to_nm))
    board.Add(cap_vert2)
    
    # Ground symbol
    ground_line_start = pcbnew.VECTOR2I(int(55 * mm_to_nm), int(30 * mm_to_nm))
    ground_line_end = pcbnew.VECTOR2I(int(55 * mm_to_nm), int(40 * mm_to_nm))
    
    ground_line = pcbnew.PCB_SHAPE(board)
    ground_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    ground_line.SetLayer(pcbnew.F_SilkS)
    ground_line.SetStart(ground_line_start)
    ground_line.SetEnd(ground_line_end)
    ground_line.SetWidth(int(0.1 * mm_to_nm))
    board.Add(ground_line)
    
    # Ground triangle
    triangle_point1 = pcbnew.VECTOR2I(int(53 * mm_to_nm), int(40 * mm_to_nm))
    triangle_point2 = pcbnew.VECTOR2I(int(55 * mm_to_nm), int(45 * mm_to_nm))
    triangle_point3 = pcbnew.VECTOR2I(int(57 * mm_to_nm), int(40 * mm_to_nm))
    
    # Draw ground triangle as three lines
    tri1_start = triangle_point1
    tri1_end = triangle_point2
    
    tri1 = pcbnew.PCB_SHAPE(board)
    tri1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    tri1.SetLayer(pcbnew.F_SilkS)
    tri1.SetStart(tri1_start)
    tri1.SetEnd(tri1_end)
    tri1.SetWidth(int(0.1 * mm_to_nm))
    board.Add(tri1)
    
    tri2_start = triangle_point2
    tri2_end = triangle_point3
    
    tri2 = pcbnew.PCB_SHAPE(board)
    tri2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    tri2.SetLayer(pcbnew.F_SilkS)
    tri2.SetStart(tri2_start)
    tri2.SetEnd(tri2_end)
    tri2.SetWidth(int(0.1 * mm_to_nm))
    board.Add(tri2)
    
    tri3_start = triangle_point3
    tri3_end = triangle_point1
    
    tri3 = pcbnew.PCB_SHAPE(board)
    tri3.SetShape(pcbnew.SHAPE_T_SEGMENT)
    tri3.SetLayer(pcbnew.F_SilkS)
    tri3.SetStart(tri3_start)
    tri3.SetEnd(tri3_end)
    tri3.SetWidth(int(0.1 * mm_to_nm))
    board.Add(tri3)
    
    # Output terminal (right side)
    output_start = pcbnew.VECTOR2I(int(55 * mm_to_nm), int(30 * mm_to_nm))  # Start at (55mm, 30mm)
    output_end = pcbnew.VECTOR2I(int(65 * mm_to_nm), int(30 * mm_to_nm))   # End at (65mm, 30mm)
    
    # Create output line
    output_line = pcbnew.PCB_SHAPE(board)
    output_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    output_line.SetLayer(pcbnew.F_SilkS)
    output_line.SetStart(output_start)
    output_line.SetEnd(output_end)
    output_line.SetWidth(int(0.1 * mm_to_nm))
    board.Add(output_line)
    
    # Add text labels
    # R1 label
    r1_text = pcbnew.PCB_TEXT(board)
    r1_text.SetText("R1")
    r1_text.SetLayer(pcbnew.F_SilkS)
    r1_text.SetTextPosition(pcbnew.VECTOR2I(int(25 * mm_to_nm), int(35 * mm_to_nm)))
    r1_text.SetTextSize(pcbnew.VECTOR2I(int(1.5 * mm_to_nm), int(1.5 * mm_to_nm)))
    r1_text.SetTextThickness(int(0.3 * mm_to_nm))
    board.Add(r1_text)
    
    # C1 label
    c1_text = pcbnew.PCB_TEXT(board)
    c1_text.SetText("C1")
    c1_text.SetLayer(pcbnew.F_SilkS)
    c1_text.SetTextPosition(pcbnew.VECTOR2I(int(45 * mm_to_nm), int(20 * mm_to_nm)))
    c1_text.SetTextSize(pcbnew.VECTOR2I(int(1.5 * mm_to_nm), int(1.5 * mm_to_nm)))
    c1_text.SetTextThickness(int(0.3 * mm_to_nm))
    board.Add(c1_text)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\circuit-1.kicad_pcb', board)

# Run the function to create the board
create_rc_filter_board()
