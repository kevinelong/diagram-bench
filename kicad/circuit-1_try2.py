import pcbnew

def create_rc_filter_board():
    # Create empty board
    board = pcbnew.CreateEmptyBoard()
    
    # Set board dimensions (0 to 90 mm horizontally, 0 to 60 mm vertically)
    board.SetWidth(pcbnew.FromMM(90))
    board.SetHeight(pcbnew.FromMM(60))
    
    # Define coordinates in nanometers
    mm_to_nm = 1000000
    
    # Input terminal (at left side)
    input_term_start = pcbnew.VECTOR2I(0, 30 * mm_to_nm)
    input_term_end = pcbnew.VECTOR2I(10 * mm_to_nm, 30 * mm_to_nm)
    
    # Create input terminal line
    input_line = pcbnew.PCB_SHAPE(board)
    input_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    input_line.SetLayer(pcbnew.F_SilkS)
    input_line.SetStart(input_term_start)
    input_line.SetEnd(input_term_end)
    input_line.SetWidth(100000)  # 0.1 mm width
    board.Add(input_line)
    
    # Resistor symbol (horizontal line with zigzag pattern)
    res_start = pcbnew.VECTOR2I(10 * mm_to_nm, 30 * mm_to_nm)
    res_end = pcbnew.VECTOR2I(30 * mm_to_nm, 30 * mm_to_nm)
    
    # Create resistor line
    res_line = pcbnew.PCB_SHAPE(board)
    res_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    res_line.SetLayer(pcbnew.F_SilkS)
    res_line.SetStart(res_start)
    res_line.SetEnd(res_end)
    res_line.SetWidth(100000)  # 0.1 mm width
    board.Add(res_line)
    
    # Draw resistor zigzag pattern
    zigzag_points = [
        (10 * mm_to_nm, 25 * mm_to_nm),
        (12 * mm_to_nm, 35 * mm_to_nm),
        (14 * mm_to_nm, 25 * mm_to_nm),
        (16 * mm_to_nm, 35 * mm_to_nm),
        (18 * mm_to_nm, 25 * mm_to_nm),
        (20 * mm_to_nm, 35 * mm_to_nm),
        (22 * mm_to_nm, 25 * mm_to_nm),
        (24 * mm_to_nm, 35 * mm_to_nm),
        (26 * mm_to_nm, 25 * mm_to_nm),
        (28 * mm_to_nm, 35 * mm_to_nm)
    ]
    
    for i in range(len(zigzag_points) - 1):
        zigzag_line = pcbnew.PCB_SHAPE(board)
        zigzag_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        zigzag_line.SetLayer(pcbnew.F_SilkS)
        zigzag_line.SetStart(pcbnew.VECTOR2I(zigzag_points[i][0], zigzag_points[i][1]))
        zigzag_line.SetEnd(pcbnew.VECTOR2I(zigzag_points[i+1][0], zigzag_points[i+1][1]))
        zigzag_line.SetWidth(100000)  # 0.1 mm width
        board.Add(zigzag_line)
    
    # Capacitor symbol (two parallel lines)
    cap_start = pcbnew.VECTOR2I(30 * mm_to_nm, 30 * mm_to_nm)
    cap_end = pcbnew.VECTOR2I(50 * mm_to_nm, 30 * mm_to_nm)
    
    # Create capacitor line
    cap_line = pcbnew.PCB_SHAPE(board)
    cap_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    cap_line.SetLayer(pcbnew.F_SilkS)
    cap_line.SetStart(cap_start)
    cap_line.SetEnd(cap_end)
    cap_line.SetWidth(100000)  # 0.1 mm width
    board.Add(cap_line)
    
    # Draw capacitor plates (vertical lines)
    plate1_start = pcbnew.VECTOR2I(35 * mm_to_nm, 25 * mm_to_nm)
    plate1_end = pcbnew.VECTOR2I(35 * mm_to_nm, 35 * mm_to_nm)
    
    plate1_line = pcbnew.PCB_SHAPE(board)
    plate1_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    plate1_line.SetLayer(pcbnew.F_SilkS)
    plate1_line.SetStart(plate1_start)
    plate1_line.SetEnd(plate1_end)
    plate1_line.SetWidth(100000)  # 0.1 mm width
    board.Add(plate1_line)
    
    plate2_start = pcbnew.VECTOR2I(45 * mm_to_nm, 25 * mm_to_nm)
    plate2_end = pcbnew.VECTOR2I(45 * mm_to_nm, 35 * mm_to_nm)
    
    plate2_line = pcbnew.PCB_SHAPE(board)
    plate2_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    plate2_line.SetLayer(pcbnew.F_SilkS)
    plate2_line.SetStart(plate2_start)
    plate2_line.SetEnd(plate2_end)
    plate2_line.SetWidth(100000)  # 0.1 mm width
    board.Add(plate2_line)
    
    # Output terminal (at right side)
    output_term_start = pcbnew.VECTOR2I(50 * mm_to_nm, 30 * mm_to_nm)
    output_term_end = pcbnew.VECTOR2I(60 * mm_to_nm, 30 * mm_to_nm)
    
    # Create output terminal line
    output_line = pcbnew.PCB_SHAPE(board)
    output_line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    output_line.SetLayer(pcbnew.F_SilkS)
    output_line.SetStart(output_term_start)
    output_line.SetEnd(output_term_end)
    output_line.SetWidth(100000)  # 0.1 mm width
    board.Add(output_line)
    
    # Ground symbol (triangle and horizontal line)
    ground_triangle_start = pcbnew.VECTOR2I(50 * mm_to_nm, 30 * mm_to_nm)
    ground_triangle_end = pcbnew.VECTOR2I(55 * mm_to_nm, 35 * mm_to_nm)
    
    # Create ground triangle
    ground_line1 = pcbnew.PCB_SHAPE(board)
    ground_line1.SetShape(pcbnew.SHAPE_T_SEGMENT)
    ground_line1.SetLayer(pcbnew.F_SilkS)
    ground_line1.SetStart(ground_triangle_start)
    ground_line1.SetEnd(ground_triangle_end)
    ground_line1.SetWidth(100000)  # 0.1 mm width
    board.Add(ground_line1)
    
    ground_triangle_end2 = pcbnew.VECTOR2I(55 * mm_to_nm, 30 * mm_to_nm)
    
    ground_line2 = pcbnew.PCB_SHAPE(board)
    ground_line2.SetShape(pcbnew.SHAPE_T_SEGMENT)
    ground_line2.SetLayer(pcbnew.F_SilkS)
    ground_line2.SetStart(ground_triangle_end)
    ground_line2.SetEnd(ground_triangle_end2)
    ground_line2.SetWidth(100000)  # 0.1 mm width
    board.Add(ground_line2)
    
    ground_horizontal_start = pcbnew.VECTOR2I(53 * mm_to_nm, 30 * mm_to_nm)
    ground_horizontal_end = pcbnew.VECTOR2I(57 * mm_to_nm, 30 * mm_to_nm)
    
    ground_line3 = pcbnew.PCB_SHAPE(board)
    ground_line3.SetShape(pcbnew.SHAPE_T_SEGMENT)
    ground_line3.SetLayer(pcbnew.F_SilkS)
    ground_line3.SetStart(ground_horizontal_start)
    ground_line3.SetEnd(ground_horizontal_end)
    ground_line3.SetWidth(100000)  # 0.1 mm width
    board.Add(ground_line3)
    
    # Add text labels
    
    # R1 label
    r1_text = pcbnew.PCB_TEXT(board)
    r1_text.SetText("R1")
    r1_text.SetLayer(pcbnew.F_SilkS)
    r1_text.SetTextPosition(pcbnew.VECTOR2I(20 * mm_to_nm, 20 * mm_to_nm))
    r1_text.SetTextSize(pcbnew.VECTOR2I(1000000, 1000000))  # 1mm x 1mm
    r1_text.SetTextThickness(100000)  # 0.1 mm thickness
    board.Add(r1_text)
    
    # C1 label
    c1_text = pcbnew.PCB_TEXT(board)
    c1_text.SetText("C1")
    c1_text.SetLayer(pcbnew.F_SilkS)
    c1_text.SetTextPosition(pcbnew.VECTOR2I(40 * mm_to_nm, 20 * mm_to_nm))
    c1_text.SetTextSize(pcbnew.VECTOR2I(1000000, 1000000))  # 1mm x 1mm
    c1_text.SetTextThickness(100000)  # 0.1 mm thickness
    board.Add(c1_text)
    
    # Save the board
    pcbnew.SaveBoard(r'C:\Users\kevin\ai_bench\image-models-20261020\kicad\circuit-1.kicad_pcb', board)

# Run the function
create_rc_filter_board()
