"""Create a commit-ready patch/source snapshot without changing the real index."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--scope',type=Path,default=Path('deploy/m10/release-files.json'))
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    declaration=json.loads((root/args.scope).read_text('utf-8'));files=declaration['files']
    if len(files)!=len(set(files)) or not files:raise ValueError('Invalid release scope')
    for name in files:
        path=root/name
        if (Path(name).is_absolute() or not path.resolve().is_relative_to(root)
                or any(part in {'.git','.idea','data','artifacts','.venv'} or part.startswith('.env') for part in Path(name).parts)
                or any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file()):
            raise ValueError('Release scope contains unsafe/private paths')
    destination=args.directory.resolve();destination.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,GIT_OPTIONAL_LOCKS='0')
    def git(*arguments,environment=None):
        return subprocess.check_output(['git','--no-optional-locks',*arguments],cwd=root,env=environment or env)
    index=Path(git('rev-parse','--git-path','index').decode().strip())
    if not index.is_absolute():index=root/index
    before=digest(index) if index.exists() else None
    base=git('rev-parse','HEAD').decode().strip()
    temporary=dict(env,GIT_INDEX_FILE=str(destination/'candidate.index'))
    git('read-tree',base,environment=temporary)
    git('add','--',*files,environment=temporary)
    git('diff','--cached','--check',environment=temporary)
    tree=git('write-tree',environment=temporary).decode().strip()
    patch=destination/'candidate.patch'
    patch.write_bytes(git('diff','--cached','--binary','--full-index',base,environment=temporary))
    archive=destination/f"cdu-opportunity-radar-{declaration['version']}-src.zip"
    with archive.open('xb') as stream:
        subprocess.run(['git','--no-optional-locks','archive','--format=zip',
                        '--prefix=cdu-opportunity-radar-'+declaration['version']+'/',tree],cwd=root,env=env,stdout=stream,check=True)
    after=digest(index) if index.exists() else None
    if before!=after:raise ValueError('Real Git index changed')
    manifest=dict(version=declaration['version'],base_commit=base,candidate_tree=tree,
        real_index_unchanged=True,scope_worktree_sha256={name:digest(root/name) for name in files},
        candidate_patch_sha256=digest(patch),source_zip=archive.name,source_zip_sha256=digest(archive),
        excludes='Private data/config/labels/credentials, IDE staging and unrelated untracked M3-B drafts',
        committed=False,pushed=False,deployed=False)
    (destination/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:manifest[key] for key in ('version','base_commit','candidate_tree','real_index_unchanged','committed','pushed','deployed')}))


if __name__=='__main__':main()
