from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'_common'))
from bjt_full_guard import main
if __name__=='__main__':raise SystemExit(main(ROOT,['--repair','--max-evals','50',*sys.argv[1:]]))
