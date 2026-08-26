"""负向登录用例：登录后校验不应在点击步失败。"""

from modules.auth.auth_batch_helpers import login_failure_expected_for_case


def test_cross_input_case_detected() -> None:
    assert login_failure_expected_for_case(
        "账号密码交叉输入",
        ["账号框输入密码", "密码框输入账号", "点击登录"],
    )


def test_positive_login_case_not_detected() -> None:
    assert not login_failure_expected_for_case(
        "正常登录",
        ["输入账号", "输入密码", "点击登录"],
    )
