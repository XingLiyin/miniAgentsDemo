import sys
import platform

print(f"Python: {sys.version}")
print(f"OS: {platform.system()} {platform.release()}")
print(f"Args: {sys.argv[1:]}")
