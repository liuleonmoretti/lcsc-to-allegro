"""Independent lexical audit: pin identifiers are text, not numeric values.

This scanner neither imports the producer loader nor calls its atom parser.
Parenthesis depth prevents text/comments/nested metadata from becoming pins.
"""
import json,re
import sexpdata

_TOKEN=re.compile(r'\s+|;[^\r\n]*|"(?:\\.|[^"\\])*"|[()]|[^()\s";]+')


def identifiers(text):
    frames=[];pins=[];position=0;roots=0
    for match in _TOKEN.finditer(text):
        if match.start()!=position:raise ValueError('Reference malformed source token')
        position=match.end();token=match.group()
        if token.isspace() or token.startswith(';'):continue
        if token=='(':
            if frames:
                parent=frames[-1]
                if len(frames)==2 and parent['head']=='pad' and parent['count']==1:
                    raise ValueError('Reference pad number cannot be a list')
                parent['count']+=1
            else:roots+=1
            frames.append(dict(head=None,count=0,pin=False));continue
        if token==')':
            if not frames:raise ValueError('Reference unbalanced source expression')
            if len(frames)==2 and frames[-1]['head']=='pad' and not frames[-1]['pin']:
                raise ValueError('Reference pad number missing')
            frames.pop();continue
        if not frames:raise ValueError('Reference unexpected token outside footprint')
        frame=frames[-1]
        if frame['count']==0:
            frame['head']=json.loads(token) if token.startswith('"') else token
        elif len(frames)==2 and frame['head']=='pad' and frame['count']==1:
            # Supported identifiers use literal ASCII characters; do not apply
            # JSON's extra \u escape semantics to a KiCad identifier.
            value=token[1:-1] if token.startswith('"') else token
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_+.-]+',value):
                raise ValueError('Reference unsupported/empty lexical pin number')
            pins.append(value);frame['pin']=True
        frame['count']+=1
    if position!=len(text) or frames or roots!=1:raise ValueError('Reference incomplete/multiple source expressions')
    return pins


def load(text):
    pins=identifiers(text);tree=sexpdata.loads(text)
    if not isinstance(tree,list):raise ValueError('Reference footprint must be a list')
    nodes=[n for n in tree[2:] if isinstance(n,list) and n and str(n[0])=='pad']
    if len(nodes)!=len(pins):raise ValueError('Reference lexical/structural pin count differs')
    for node,pin in zip(nodes,pins):node[1]=pin
    return tree
