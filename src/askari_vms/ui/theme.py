from pathlib import Path


_CHEVRON_PATH = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()


APP_STYLESHEET = """
QWidget {
    color: #15231a;
    font-family: "Segoe UI";
    font-size: 13px;
    background-color: #f4f7f4;
}
QMainWindow, QDialog, QWidget#appRoot {
    background: #f4f7f4;
}
QWidget#loginRoot {
    background: #17633f;
}
QFrame#sidebar {
    background: #17633f;
    border: none;
    border-right: 1px solid #b7c9bd;
}
QLabel#brandMark {
    background: #d8b65c;
    color: #174630;
    border-radius: 20px;
    font-size: 15px;
    font-weight: 800;
}
/* Plain containers inside the sidebar must not pick up the global widget colour. */
QWidget#brandBox, QLabel#brandLogo {
    background: transparent;
}
QLabel#brandTitle {
    color: #ffffff;
    font-size: 17px;
    font-weight: 700;
}
QLabel#brandSubtitle, QLabel#sidebarFooter {
    color: #d1e8da;
    font-size: 11px;
}
QPushButton[nav="true"] {
    background: transparent;
    border: none;
    border-radius: 8px;
    color: #e7f3eb;
    font-size: 13px;
    font-weight: 500;
    padding: 11px 13px;
    text-align: left;
}
QPushButton[nav="true"]:hover {
    background: #24764f;
    color: #ffffff;
}
QPushButton[nav="true"][active="true"] {
    background: #ffffff;
    color: #145536;
    font-weight: 700;
}
QFrame#topbar {
    background: white;
    border-bottom: 1px solid #c5d3c9;
}
QFrame#dialogHeader {
    background: #f4f8f5;
    border-bottom: 1px solid #c5d3c9;
}
QLabel#dialogTitle {
    color: #102d20;
    font-size: 21px;
    font-weight: 750;
}
QLabel#shortcutPanel {
    background: #f6f9f7;
    color: #2b4436;
    border: 1px solid #c9d7ce;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 12px;
}
QLabel#formError {
    background: #fde8e8;
    color: #9b2525;
    border: 1px solid #e4adad;
    border-radius: 7px;
    padding: 8px 10px;
    font-weight: 600;
}
QLabel#infoBanner {
    background: #e8f1ff;
    color: #24558c;
    border: 1px solid #b7cce8;
    border-radius: 8px;
    padding: 10px 12px;
}
QLabel#pageTitle {
    color: #102d20;
    font-size: 22px;
    font-weight: 750;
}
QLabel#pageEyebrow {
    color: #6f8177;
    font-size: 11px;
    font-weight: 600;
}
QLabel#userAvatar {
    background: #e8efe9;
    color: #174630;
    border-radius: 18px;
    font-weight: 800;
}
QLabel#userName {
    color: #173526;
    font-weight: 700;
}
QLabel#muted, QLabel[muted="true"] {
    color: #718077;
}
QFrame[card="true"] {
    background: white;
    border: 1px solid #c5d4ca;
    border-radius: 12px;
}
QFrame#bulkBar {
    background: #edf5ef;
    border: 1px solid #b9cdbf;
    border-radius: 9px;
}
QFrame[card="true"] QLabel, QFrame#topbar QLabel, QFrame#dialogHeader QLabel,
QFrame#sidebar QLabel, QFrame#sidebar QPushButton {
    background: transparent;
}
QLabel[metric="true"] {
    color: #112f20;
    font-size: 28px;
    font-weight: 800;
}
QLabel[section="true"] {
    color: #112f20;
    font-size: 16px;
    font-weight: 750;
}
QLabel[status="online"] {
    background: #dff4e6;
    color: #17643a;
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 700;
}
QLabel[status="warning"] {
    background: #e7efff;
    color: #2455a6;
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 700;
}
QFrame#doorPanel {
    background: #f8fbf9;
    border: 1px solid #c4d3c9;
    border-radius: 9px;
}
QFrame#doorPanel QLabel, QFrame#doorPanel QPushButton { background: transparent; }
QLabel[doorState="closed"] {
    background: #e8efe9;
    color: #42574a;
    border: 1px solid #c4d0c7;
    border-radius: 9px;
    padding: 4px 9px;
    font-weight: 700;
}
QLabel[doorState="open"], QLabel[doorState="unlocked"] {
    background: #dff4e6;
    color: #17643a;
    border: 1px solid #acd3ba;
    border-radius: 9px;
    padding: 4px 9px;
    font-weight: 700;
}
QLabel[doorState="locked"] {
    background: #fff0d9;
    color: #8a5717;
    border: 1px solid #e2c28e;
    border-radius: 9px;
    padding: 4px 9px;
    font-weight: 700;
}
QPushButton#primaryButton {
    background: #0f5b3b;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 700;
}
QPushButton#primaryButton:hover { background: #0a482e; }
QPushButton#dangerButton {
    background: #fff5f5;
    color: #a12b2b;
    border: 1px solid #dca6a6;
}
QPushButton#dangerButton:hover { background: #fde8e8; }
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox, QTextEdit {
    background: white;
    border: 1px solid #aebfb4;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: #236846;
}
QComboBox QAbstractItemView {
    background: white;
    color: #15231a;
    selection-background-color: #dfeee4;
    selection-color: #102d20;
}
QComboBox {
    padding-right: 38px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 32px;
    background: #edf5ef;
    border: none;
    border-left: 1px solid #b7c8bd;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}
QComboBox::drop-down:hover {
    background: #dfeee4;
}
QComboBox::down-arrow {
    image: url(__CHEVRON_PATH__);
    width: 14px;
    height: 8px;
}
QCheckBox::indicator, QTableWidget::indicator {
    width: 16px;
    height: 16px;
    background: #ffffff;
    border: 2px solid #6f8276;
    border-radius: 3px;
}
QCheckBox::indicator:hover, QTableWidget::indicator:hover {
    border-color: #17633f;
}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {
    background: #17633f;
    border: 2px solid #0f4f31;
}
QCheckBox::indicator:disabled, QTableWidget::indicator:disabled {
    background: #e7ece8;
    border-color: #aebbb3;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus, QTextEdit:focus {
    border: 1px solid #17613f;
}
QLineEdit[ocrConfidence="low"] {
    background: #fff5d9;
    border: 2px solid #d39b32;
}
QTableWidget {
    background: white;
    alternate-background-color: #f8faf8;
    border: 1px solid #b9c9bf;
    border-radius: 10px;
    gridline-color: #cad6ce;
    selection-background-color: #d9eadf;
    selection-color: #102d20;
}
QHeaderView::section {
    background: #edf3ee;
    color: #314b3b;
    border: none;
    border-right: 1px solid #c1d0c6;
    border-bottom: 1px solid #b7c8bd;
    padding: 10px 8px;
    font-weight: 700;
}
/* The viewport cannot be clipped to the table's radius, so the end sections carry
   matching corners instead of squaring off over the rounded border. */
QHeaderView::section:first {
    border-top-left-radius: 9px;
}
QHeaderView::section:last {
    border-top-right-radius: 9px;
    border-right: none;
}
QDialogButtonBox QPushButton, QPushButton {
    min-height: 30px;
    background-color: #ffffff;
    color: #173526;
    border: 1px solid #aebfb4;
    border-radius: 7px;
    padding: 5px 12px;
}
QDialogButtonBox QPushButton:hover, QPushButton:hover { background-color: #eef5f0; }
QLabel#shortcutHeading { color: #9b2525; font-weight: 800; }
QPushButton#categoryButton { font-weight: 600; }
QPushButton#categoryButton[pageActive="true"] {
    background: #17633f;
    color: #ffffff;
    border: 1px solid #0f4f31;
    font-weight: 800;
}
QFrame#capturePanel {
    background: #f5f8f6;
    border: 1px dashed #b6c8bd;
    border-radius: 9px;
}
QFrame#capturePanel[captured="true"] {
    background: #eef8f1;
    border: 1px solid #7fb894;
}
QFrame#capturePanel QLabel { background: transparent; }
QLabel#captureTitle { color: #2b4436; font-weight: 700; }
QFrame#statTile {
    background: #17633f;
    border: 1px solid #0f4f31;
    border-radius: 10px;
    min-width: 190px;
}
QFrame#statTile QLabel { background: transparent; }
QLabel#statCaption { color: #d6ebdf; font-size: 12px; font-weight: 600; }
QLabel#statValue { color: #ffffff; font-size: 26px; font-weight: 800; }
QFrame#groupTile QLabel { background: transparent; }
QLabel#groupValue { color: #112f20; font-size: 19px; font-weight: 800; }
QWidget#pager, QWidget#pagerButtons { background: transparent; }
QWidget#pager QPushButton { min-height: 26px; padding: 3px 10px; }
QWidget#pager QPushButton[pageActive="true"] {
    background: #17633f;
    color: #ffffff;
    border: 1px solid #0f4f31;
    font-weight: 700;
}
QWidget#pager QPushButton:disabled { color: #9baba1; background: #f2f5f3; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
""".replace("__CHEVRON_PATH__", _CHEVRON_PATH)
