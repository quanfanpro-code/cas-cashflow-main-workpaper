"""用Windows选择窗口收集固定输入，取消时不留下半份配置。"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from tkinter import Tk, filedialog, messagebox, simpledialog


class SelectionCancelled(RuntimeError):
    pass


def _materiality_minor(raw: str) -> int:
    """把用户输入的重要性金额（元）四舍五入为最小货币单位整数。"""
    amount = Decimal(raw.replace(",", "").replace("，", "").strip())
    if amount <= 0:
        raise ValueError("实际执行重要性水平必须是正数")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def select_manifest() -> dict[str, object]:
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        prompts = (
            ("audited_balance_sheet_path", "选择审定资产负债表"),
            ("audited_income_statement_path", "选择审定利润表"),
            ("trial_balance_path", "选择科目余额表"),
            ("journal_pairs_path", "选择一借一贷明细"),
            ("prior_cashflow_path", "选择上年度审定现金流量表"),
        )
        result = {}
        for key, title in prompts:
            value = filedialog.askopenfilename(title=title, filetypes=[("Excel文件", "*.xlsx *.xlsm"), ("全部文件", "*.*")])
            if not value:
                raise SelectionCancelled("用户取消了文件选择")
            result[key] = value
        optional_prompts = (
            ("book_to_report_adjustments_path", "账表调整明细"),
            ("audit_adjustments_path", "审计调整明细"),
        )
        for key, label in optional_prompts:
            if not messagebox.askyesno(
                "可选资料",
                f"本次是否提供{label}？",
                parent=root,
            ):
                continue
            value = filedialog.askopenfilename(
                title=f"选择{label}",
                filetypes=[("Excel文件", "*.xlsx *.xlsm"), ("全部文件", "*.*")],
            )
            if not value:
                raise SelectionCancelled(f"用户取消了{label}选择")
            result[key] = value
        output_dir = filedialog.askdirectory(title="选择输出文件夹")
        if not output_dir:
            raise SelectionCancelled("用户取消了输出文件夹选择")
        raw = simpledialog.askstring("实际执行重要性水平", "请输入实际执行重要性水平（元）：", parent=root)
        if raw is None:
            raise SelectionCancelled("用户取消了重要性水平输入")
        try:
            materiality_minor = _materiality_minor(raw)
        except InvalidOperation as exc:
            raise ValueError("实际执行重要性水平必须是正数") from exc
        entity_name = simpledialog.askstring("企业名称", "请输入编制单位名称：", parent=root)
        if entity_name is None:
            raise SelectionCancelled("用户取消了企业名称输入")
        period = simpledialog.askstring("所属期间", "请输入所属期间，例如2025年度：", parent=root)
        if period is None:
            raise SelectionCancelled("用户取消了所属期间输入")
        if not entity_name.strip() or not period.strip():
            raise ValueError("企业名称和所属期间不得为空")
        result.update({
            "output_dir": output_dir,
            "display_unit": "元",
            "currency": "人民币",
            "performance_materiality_minor": materiality_minor,
            "entity_name": entity_name.strip(),
            "period": period.strip(),
        })
        return result
    except (SelectionCancelled, ValueError) as exc:
        messagebox.showinfo("现金流量表主表工作底稿", str(exc), parent=root)
        raise
    finally:
        root.destroy()
