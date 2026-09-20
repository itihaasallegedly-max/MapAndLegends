import os
import google.genai as genai
os.environ["GEMINI_API_KEY"] = "dummy"
import inspect
print(inspect.signature(genai.Client().files.upload))
