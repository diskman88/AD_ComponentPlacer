object ClassPlacerProgress: TClassPlacerProgress
  Left = 300
  Top = 260
  BorderStyle = bsDialog
  Caption = 'Class Placer 0.4 - Waiting for external calculation'
  ClientHeight = 145
  ClientWidth = 600
  Position = poScreenCenter
  Visible = False
  OnClose = FormClose
  object StageLabel: TLabel
    Left = 18
    Top = 18
    Width = 564
    Height = 60
    AutoSize = False
    WordWrap = True
    Caption = 'Starting...'
  end
  object CancelButton: TButton
    Left = 460
    Top = 96
    Width = 120
    Height = 30
    Caption = 'Cancel batch'
    Cancel = True
    OnClick = CancelButtonClick
  end
  object PollTimer: TTimer
    Enabled = False
    Interval = 500
    OnTimer = PollTimerTimer
    Left = 24
    Top = 100
  end
end
