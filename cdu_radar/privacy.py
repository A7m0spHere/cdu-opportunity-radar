"""Small offline sanitizer; not a complete personal-name recognition system."""
import re
from bs4 import BeautifulSoup


def sanitize(text,remove_identity_lines=True):
    text=text or ""
    if re.search(r"</?[A-Za-z][^>]*>",text):
        soup=BeautifulSoup(text,"html.parser")
        for node in soup.select("script,style,iframe,noscript"):node.decompose()
        for block in soup.find_all(["p","div","li","h1","h2","h3","tr"]):block.append("\n")
        for br in soup.find_all("br"):br.replace_with("\n")
        plain=soup.get_text("")
    else:plain=text
    lines=[]
    for line in plain.splitlines():
        if remove_identity_lines and re.search(r"联系人|联系电话|联系邮箱|联系方式|身份证|学号\s*[：:]|姓名\s*[：:]|拟推荐|推荐人选|候选人|获奖成员|指导老师|指导教师|编辑\s*[：:]",line):
            lines.append("[联系方式或身份信息已移除]")
            continue
        line=re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}","[邮箱已移除]",line)
        line=re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)","[手机号已移除]",line)
        line=re.sub(r"(?<!\d)\d{17}[\dXx](?!\w)","[身份号码已移除]",line)
        line=re.sub(r"(?<!\d)0\d{2,3}[-—]?\d{7,8}(?!\d)","[电话已移除]",line)
        line=re.sub(r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})","[凭据已移除]",line)
        lines.append(line.strip())
    return "\n".join(line for line in lines if line)


def spreadsheet_text(text):
    return "'"+text if text.lstrip().startswith(("=","+","-","@")) else text
