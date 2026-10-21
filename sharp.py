import sys
import cv2, numpy as np
from pathlib import Path
for p in sys.argv[1:]:
    img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    lap = cv2.Laplacian(img, cv2.CV_64F)
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    print('%-26s laplacian_var=%8.1f  mean|grad|=%6.2f  p99|grad|=%6.0f' % (
        Path(p).name, lap.var(), float(np.abs(gx).mean()+np.abs(gy).mean())/2, float(np.percentile(np.abs(gx),99))))
