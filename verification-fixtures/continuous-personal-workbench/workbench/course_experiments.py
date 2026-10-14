"""Source preparation for this personal controller and an explicit customer."""
import json, os
from pathlib import Path
from .course_continuity import source_files
from .execution import require_unlinked_path
def copy_experiment_sources(destination):
    root=Path(__file__).resolve().parents[1]
    value=os.environ.get('FLOWERP_PROJECT_ROOT')
    if not value: raise ValueError('FLOWERP_PROJECT_ROOT must name the independent customer source')
    product=Path(value).resolve(strict=True)
    require_unlinked_path(value); require_unlinked_path(destination)
    destination=Path(destination).resolve()
    if destination==root or destination==product or root.is_relative_to(destination) or product.is_relative_to(destination):
        raise ValueError('source directory cannot be an experiment target')
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError('refuse to replace an existing experiment')
    if not (product/'flowerp').is_dir(): raise ValueError('customer package missing')
    control,_=source_files(root,('workbench','eval','agent','tests'),('pyproject.toml','AGENTS.md'))
    customer,_=source_files(product,('flowerp',))
    files={**control,**customer}
    if set(control)&set(customer): raise ValueError('source names collide')
    destination.mkdir(parents=True,exist_ok=True)
    for name,entry in files.items():
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(Path(entry['source']).read_bytes())
    marker=destination/'.course/experiment-source.json'
    marker.parent.mkdir()
    data={'schema':'personal.experiment-source/v1','control_root':str(root),'product_root':str(product),
          'files':files,'human_review':'pending_human_review'}
    with marker.open('x',encoding='utf8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    return data

def ensure_product_process(script):
    """Explicit process delegation used only by supplemental teaching examples."""
    import subprocess, sys
    product=Path(os.environ['FLOWERP_PROJECT_ROOT']).resolve(strict=True)
    interpreter=product/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if Path(sys.prefix).resolve()==interpreter.parent.parent.resolve(): return
    if not interpreter.is_file(): raise FileNotFoundError('independent customer interpreter missing')
    result=subprocess.run([str(interpreter),'-B','-X','utf8',str(Path(script).resolve()),*sys.argv[1:]],
                          cwd=Path.cwd(),env=os.environ.copy())
    raise SystemExit(result.returncode)
