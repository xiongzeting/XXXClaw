"""Natural user input fidelity cases; the runtime adapter is applied by the runner."""
from build_dialogue_campaign import add, CASES, write_suite, hidden_test, edits_only
import json

examples = [
    ("literal_double_space", "text", "welcome.txt", "Hello  world",
     '请创建 welcome.txt，文件内容严格为引号里的文字，不含引号、不添加换行："Hello  world"。这是展示文案，保留其中所有空格。'),
    ("literal_tab", "text", "columns.tsv", "name\tvalue\nalpha\t0\n",
     '请创建 columns.tsv，内容为下面代码块里的 TSV，保留制表符和换行（末尾一个换行）：\n```text\nname\tvalue\nalpha\t0\n```'),
    ("markdown_hardbreak", "post", "notice.md", "first line  \nsecond line\n",
     '请创建 notice.md，逐字保存下面代码块的 Markdown；第一行末尾两个空格是强制换行，不能删；末尾一个换行：\n```markdown\nfirst line  \nsecond line\n```'),
    ("multiline_string", "text", "payload.json", None,
     '请创建 payload.json，只有一个字段 message，字段内容就是引号内的三行文字，不含引号："alpha\n\nbeta"。这是一个带空白行的原始消息，请逐字保留。'),
    ("sql_literal_spaces", "post", "query.sql", "SELECT 'A   B' AS label;\n",
     '请把下面 SQL 原样保存到 query.sql，保留字符串里的空格，文件末尾一个换行：\n```sql\nSELECT \'A   B\' AS label;\n```'),
    ("python_indent_semantics", "text", "app.py", None,
     '请把以下函数原样保存到 app.py，不要调整控制流和缩进：\n```python\ndef selected(flag):\n    result = "outer"\n    if flag:\n        result = "inner"\n    return result\n```'),
    ("poem_blank_lines", "post", "poem.txt", "sunrise\n\nsunset\n",
     '请将下面短诗按原样保存到 poem.txt，保留空行，末尾一个换行：\n```text\nsunrise\n\nsunset\n```'),
    ("aligned_significant_spaces", "text", "fixed-width.txt", "ID   VALUE\nA1   0030\n",
     '导入器使用定长字段，请创建 fixed-width.txt，严格保存下面内容及空格，末尾一个换行：\n```text\nID   VALUE\nA1   0030\n```'),
]
for key, mode, path, expected, prompt in examples:
    checks=[edits_only(path)]
    if expected is not None:
        checks.append({"type":"file_equals","path":path,"text":expected,"dimension":"outcome"})
    elif key == "multiline_string":
        checks.append(hidden_test("import json\nassert json.load(open('payload.json'))=={'message':'alpha\\n\\nbeta'}"))
    else:
        checks.append(hidden_test("from app import selected\nassert selected(False)=='outer'\nassert selected(True)=='inner'"))
    add("feishu_"+key,"input-fidelity",[prompt],checks=checks,shared=True)
    CASES[-1]["source"]["input_adapter"]="feishu-"+mode
    CASES[-1]["source"]["failure_family"]="feishu-whitespace-normalization"

write_suite("dialogue-input-development", CASES)
print(f"Built {len(CASES)} natural Feishu input cases")
