"""Producer-side S-expression loading with lexical pad identifiers.

Dimensions retain sexpdata's numeric types. Only pad-number fields come from a
second parse whose atoms never undergo numeric/boolean conversion.
"""
import sexpdata


class _LiteralAtoms(sexpdata.Parser):
    def atom(self,token):return sexpdata.Symbol(token)


def load(text):
    tree=sexpdata.loads(text)
    literal=_LiteralAtoms(text).parse()
    if len(literal)!=1 or not isinstance(tree,list) or not isinstance(literal[0],list):
        raise ValueError('Expected one footprint expression')
    lexical=literal[0]
    if len(tree)!=len(lexical):raise ValueError('Source expression structure differs during identifier decoding')
    for node,raw in zip(tree[2:],lexical[2:]):
        if not isinstance(node,list) or not node or str(node[0])!='pad':continue
        if not isinstance(raw,list) or len(raw)<2 or isinstance(raw[1],list) or not isinstance(raw[1],(str,sexpdata.Symbol)):
            raise ValueError('Pad number must be a string token')
        node[1]=str(raw[1])
    return tree
