"""Reproducible extension artifact, no credentials or developer fixtures."""
from pathlib import Path
import zipfile
root=Path(__file__).resolve().parent
output=root.parent/'frontend/admin/public/downloads/lingcast-collector.zip'
output.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((root/'chrome').iterdir()):
        if path.is_file() and path.suffix in ('.js','.json','.html','.css','.md'):
            info=zipfile.ZipInfo('lingcast-collector/'+path.name,(2026,10,28,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,path.read_bytes())
print(output)
