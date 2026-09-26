import sys


def main():
    import cv2
    import nicegui
    import numpy
    import onnxruntime

    print("BAAH deps ok", cv2.__version__, numpy.__version__, onnxruntime.__version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
