使用説明書(Confluence)編集中
https://suzukiglobalit.atlassian.net/wiki/pages/resumedraft.action?draftId=1844543544

SourceCode
http://gitweb.yrd.suzuki.co.jp/70056/vlm_rosbag_analyze

リリースノート

Ver.1.0

基本機能版 (Basic Version)

VLMによる画像解析・自動タグ付け
CSVデータベース生成
シーン検索・閲覧機能

Ver.2.0

TAG1 : ROSBAG Extract

追加機能

ROSBAG画像抽出機能を追加
ROSBAG入力ソース選択機能を追加
フォルダ一括処理モード
単一 .db3 ファイル処理モード
Sampling Strategy選択機能を追加
時間間隔指定抽出
総抽出枚数指定

改善内容

アプリケーション上から直接ROSBAG画像抽出が可能になりました。
単一の .db3 ファイルだけでなく、ROSBAGフォルダ配下の複数 .db3 ファイルを時系列順に連結して処理可能になりました。
抽出間隔や抽出枚数をユーザーが自由に設定できるようになりました。

TAG2 : VLM Tagging

追加機能

VLMモデル選択用ドロップダウンメニューを追加

改善内容

アプリケーション上から使用するVLMモデルを選択可能になりました。
ローカル環境の ./models ディレクトリに配置されたモデルを利用できます。
現在は Qwen3_5ForConditionalGeneration ベースのモデルに対応しています。

TAG3 : Search & View

追加機能

重要イベントタグのハイライト表示機能を追加

対象イベント


路上駐車
対向車すれ違い
追い越し
割り込み
工事区間
狭路走行
急ブレーキ
飛び出し注意


改善内容

上記の重要イベントを検索対象として追加しました。
該当タグを持つシーンを検索結果画面で強調表示できるようになりました。

Ver.3.0

app.py更新版

TAG1 : ROSBAG Extract

追加機能

カメラ位置選択ドロップダウンメニューを追加

対応カメラ


CAMERA_TOPICS = {
"前方 Front": "/sensing/camera/camera0/image_rect_color/compressed",
"後方 Rear": "/sensing/camera/camera1/image_rect_color/compressed",
"前左 Front Left": "/sensing/camera/camera2/image_rect_color/compressed",
"後左 Rear Left": "/sensing/camera/camera3/image_rect_color/compressed",
"前右 Front Right": "/sensing/camera/camera4/image_rect_color/compressed",
"後右 Rear Right": "/sensing/camera/camera5/image_rect_color/compressed"
}


改善内容

抽出対象のROS Topicをカメラ位置ごとに選択可能になりました。
前方・後方・左右カメラの画像を個別に抽出できるようになりました。

TAG2 : VLM Tagging

追加機能

カメラ位置を考慮したプロンプト設計を導入
Camera Prompt Hint機能を追加
Camera-Aware Prompt機能を追加

実装例


Rear Camera
Vehicles visible in this image are behind the ego vehicle.
Use:
- 後続車
Do NOT use:
- 先行車
- 対向車


改善内容

カメラ位置情報をVLMに入力することで、相対的な車両関係を正しく理解できるようになりました。
後方カメラでは「後続車」、前方カメラでは「先行車」「対向車」など、視点に応じたイベント認識精度を向上しました。
マルチカメラ環境でのシーン理解性能を強化しました。

TAG3 : Search & View

追加機能

Cameraタグ表示機能を追加
Cameraフィルタ検索機能を追加
Camera情報のCSV保存機能を追加

改善内容

CSVデータベースにカメラ位置情報を保存するようになりました。
Searchページからカメラ位置別のシーン検索が可能になりました。
前方・後方・左右カメラごとのシーン分析と比較が容易になりました。

Ver.3.0 サマリー

主なアップデート

✅ マルチカメラ対応✅ カメラ位置別画像抽出機能✅ Camera-Aware VLM解析✅ Cameraタグ付きCSV生成✅ カメラ別シーン検索機能✅ 相対車両関係認識精度の向上

期待効果

ROSBAGデータからのマルチカメラシーン抽出を効率化
自動アノテーション精度の向上
特定イベント検索の高速化
自動運転データマイニング作業の省力化・効率化

以上2029/09/14