from app.services.latex_compiler import compile_latex_sync
from app.models.compile import CompileOptions
from pathlib import Path
import os

# Create a dummy tex file
content = r"""
\documentclass{article}
\begin{document}
Hello World
\end{document}
"""

with open("test.tex", "w") as f:
    f.write(content)

options = CompileOptions(passes=1)
result = compile_latex_sync(Path("test.tex").resolve(), options)

print(f"Success: {result.success}")
print(f"Log length: {len(result.log)}")
if result.success:
    print(f"PDF generated at: {result.pdf_path}")
else:
    print("Compilation failed (expected if pdflatex is missing)")
    print(result.log[:200])

# Cleanup
if os.path.exists("test.tex"):
    os.remove("test.tex")
