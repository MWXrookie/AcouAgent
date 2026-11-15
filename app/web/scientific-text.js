/* Display only. Never normalize stored requests, code, or solver parameters. */
(()=>{'use strict';
const symbols={rho:'ρ',pi:'π',omega:'ω',Omega:'Ω',lambda:'λ',alpha:'α',beta:'β',gamma:'γ',delta:'δ',Delta:'Δ',theta:'θ',phi:'φ',varphi:'φ',mu:'μ',nu:'ν',sigma:'σ',epsilon:'ε',varepsilon:'ε',eta:'η',tau:'τ',kappa:'κ',nabla:'∇',partial:'∂',infty:'∞',times:'×',cdot:'·',pm:'±',mp:'∓',le:'≤',leq:'≤',ge:'≥',geq:'≥',ne:'≠',neq:'≠',approx:'≈',equiv:'≡',sum:'Σ',prod:'Π',int:'∫',in:'∈',to:'→',rightarrow:'→',ldots:'…',dots:'…',quad:' ',qquad:' ',sin:'sin',cos:'cos',tan:'tan',exp:'exp',log:'log',ln:'ln',min:'min',max:'max',Re:'Re',Im:'Im'};
const wrappers=new Set(['text','textrm','textnormal','mathrm','mathbf','mathit','mathsf','operatorname','mbox','boldsymbol']);
const supers={'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹','-':'⁻','+':'⁺'};
const subs={'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉','-':'₋','+':'₊'};
function mathText(source){
 let i=0,depth=0,unsupported=false;
 function atom(){
  while(source[i]===' ')i++;
  if(source[i]==='{'){i++;return sequence(true)}
  if(source[i]==='\\')return command();
  return source[i++]||'';
 }
 function command(){
  i++;const found=/^[A-Za-z]+/.exec(source.slice(i));
  if(!found){const c=source[i++]||'';return /[,;:! ]/.test(c)?' ':c==='\\'?' ; ':c}
  const name=found[0];i+=name.length;
  if(wrappers.has(name))return atom();
  if(['frac','dfrac','tfrac'].includes(name))return '('+atom()+')/('+atom()+')';
  if(name==='sqrt'){
   let degree='';if(source[i]==='['){const end=source.indexOf(']',i);if(end>=0){degree=source.slice(i+1,end);i=end+1}}
   return (degree?'root('+degree+', ':'sqrt(')+atom()+')';
  }
  if(name==='left'||name==='right')return '';
  if(Object.hasOwn(symbols,name))return symbols[name];
  unsupported=true;return '\\'+name;
 }
 function sequence(group){
  if(++depth>32)throw Error('math_depth');
  let out='';
  while(i<source.length){
   const c=source[i];if(c==='}'&&group){i++;break}
   if(c==='\\'){out+=command();continue}
   if(c==='{'){i++;out+=sequence(true);continue}
   if(c==='^'||c==='_'){
    i++;const value=atom(),map=c==='^'?supers:subs;
    out+=[...value].every(x=>Object.hasOwn(map,x))?[...value].map(x=>map[x]).join(''):c==='_'&&/^[A-Za-z]+$/.test(value)?value:c+'('+value+')';continue;
   }
   i++;out+=c==='&'?', ':c==='~'?' ':c;
  }
  depth--;return out;
 }
 try{const result=sequence(false);return unsupported?source:result.replace(/\s+/g,' ').trim()}catch(e){return source}
}
function plainMathText(value){
 let text=String(value??'');if(!text.includes('\\')&&!text.includes('$'))return text;
 const protectedParts=[];
 const keep=s=>{protectedParts.push(s);return '\uE000'+(protectedParts.length-1)+'\uE001'};
 // Protect executable examples and URLs before looking for mathematical delimiters.
 text=text.replace(/(^[ \t]*(?:`{3,}|~{3,})[^\n]*\n[\s\S]*?^[ \t]*(?:`{3,}|~{3,})[^\n]*$)|(`+)[^\n]*?\2|https?:\/\/[^\s<>]+/gm,s=>keep(s));
 text=text.replace(/\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)/g,(_,a,b,c)=>mathText(a??b??c));
 text=text.replace(/(^|[^\\])\$([^$\n]+)\$/g,(all,prefix,body)=>{
  // Do not reinterpret currency prose as a formula.
  return /\\[A-Za-z]+|[=^_+*/()\-]|^[A-Za-z0-9.,]+$|^[0-9.]+\s*(?:Pa|Hz|kHz|MHz|m|mm|cm|s|ms|μs)$/.test(body)?prefix+mathText(body):all;
 });
 // Also handle common commands in older records without math delimiters.
 text=text.replace(/\\([A-Za-z]+)((?:\s*\{(?:[^{}]|\{[^{}]*\})*\})*(?:[_^](?:\{[^{}]*\}|[A-Za-z0-9+-]))*)/g,(all,name,args)=>{
  return wrappers.has(name)||Object.hasOwn(symbols,name)||['frac','dfrac','tfrac','sqrt'].includes(name)?mathText(all):all;
 });
 // Parameter evidence can contain source spans with the surrounding $ removed.
 text=text.replace(/\\[,;:! ]/g,' ').replace(/\b([A-Za-z])_(?:\{([A-Za-z0-9]+)\}|([A-Za-z0-9]))/g,(_,letter,group,single)=>{
  const value=group??single;return letter+([...value].every(x=>Object.hasOwn(subs,x))?[...value].map(x=>subs[x]).join(''):value);
 });
 return text.replace(/\uE000(\d+)\uE001/g,(_,n)=>protectedParts[Number(n)]);
}
globalThis.plainMathText=plainMathText;
})();
