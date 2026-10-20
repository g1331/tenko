/** 二次确认；用户取消或关闭对话框时 ElMessageBox 会 reject，这里统一转为 false。 */
export async function confirmAction(
  message: string,
  title: string,
  confirmButtonText = '确认',
): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, {
      type: 'warning',
      confirmButtonText,
      cancelButtonText: '取消',
    })
    return true
  } catch {
    return false
  }
}
