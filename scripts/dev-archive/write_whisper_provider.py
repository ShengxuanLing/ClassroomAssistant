import base64, pathlib, sys
data = base64.b64decode(sys.stdin.read())
pathlib.Path('src/whisper_provider.py').write_bytes(data)
print('wrote', len(data), 'bytes')
