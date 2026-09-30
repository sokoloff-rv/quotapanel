from quotabubble.ui.taskbar_menu import TaskbarMenu


def test_compact_menu_geometry_and_checkable_action(qapp):
    menu = TaskbarMenu()
    action = menu.addAction("Запускать вместе с Windows")
    action.setCheckable(True)
    action.setChecked(True)
    refresh = menu.addAction("Обновить")
    fired = []
    refresh.triggered.connect(lambda: fired.append(True))
    try:
        menu.ensurePolished()
        # Geometry is in logical pixels: scaling should not enlarge spacing.
        metrics = menu.fontMetrics()
        assert menu.sizeHint().width() - metrics.horizontalAdvance(action.text()) < 50
        assert menu.actionGeometry(action).height() - metrics.height() <= 10
        action.trigger()
        assert not action.isChecked()
        refresh.trigger()
        assert fired == [True]
    finally:
        menu.close()
