"""Group-chat conversations with real, distinct inbound actor IDs."""
from build_dialogue_campaign import add, CASES, write_suite

scenarios=[
    ("language","代码示例语言","Python","Go"),
    ("editor","默认编辑器","Vim","Emacs"),
    ("timezone","日程时区","Asia/Shanghai","Europe/London"),
    ("branch","个人实验分支","alice-lab","bob-lab"),
    ("format","报告格式","Markdown","CSV"),
    ("indent","代码缩进习惯","spaces-4","tabs"),
    ("shell","交互终端偏好","PowerShell","Bash"),
    ("contact","个人备用邮箱","alice@example.test","bob@example.test"),
]
for key,subject,a,b in scenarios:
    add("group_identity_"+key,"actor-isolation",[
        f"我是 Alice，请记住我的{subject}是 {a}。这是我的个人偏好，不是群里所有人的约定。",
        f"我是 Bob，请记住我的{subject}是 {b}。我的偏好只属于我，不要覆盖 Alice 的记录。",
        f"按我之前明确告诉你的个人记录，我的{subject}是什么？只给 JSON：value。",
        f"也查一下我之前明确告诉你的个人记录，我的{subject}是什么？只给 JSON：value。",
    ],expected={"value":b},checks=[{"type":"final_json","phase":"turn3","fields":{"value":a},"dimension":"outcome"}],shared=True)
    CASES[-1]['source'].update({"input_adapter":"feishu-router","actors":["Alice","Bob","Alice","Bob"],
        "failure_family":"shared-thread-actor-binding","actor_policy":"Personal preferences follow inbound user_id, shared thread context is not private-user identity."})
write_suite('dialogue-router-development',CASES)
print(f'Built {len(CASES)} group identity conversations')
