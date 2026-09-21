"""Bounded subprocess for third-party document parsers; writes extracted text only."""
import io,sys,resource
resource.setrlimit(resource.RLIMIT_CPU,(25,25))
try:resource.setrlimit(resource.RLIMIT_DATA,(768*1024*1024,768*1024*1024))
except (ValueError,OSError):pass
blob=sys.stdin.buffer.read(20*1024*1024+1)
if len(blob)>20*1024*1024:raise ValueError('Document byte limit exceeded')
if sys.argv[1]=='pdf':
    from pypdf import PdfReader
    value='\n\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(blob)).pages)
else:
    from docx import Document
    doc=Document(io.BytesIO(blob));value='\n'.join([p.text for p in doc.paragraphs]+[' | '.join(c.text for c in row.cells) for t in doc.tables for row in t.rows])
if len(value.encode())>40*1024*1024:raise ValueError('Expanded document limit exceeded')
sys.stdout.write(value)
