class ActivityLogError(Exception):
    """利用者に見せてよいエラー (CLI ではメッセージだけ表示し、GUI ではダイアログに出す)。"""


class NotFoundError(ActivityLogError):
    pass
