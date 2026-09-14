import os
import re
import cv2
import json
import csv
import glob
import torch
import numpy as np
import pandas as pd
from pathlib import Path
from PIL import Image
import streamlit as st
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore
from transformers import AutoProcessor, AutoModelForImageTextToText

# ==============================================================================
# 1. ページ全体設定
# ==============================================================================
st.set_page_config(
    page_title="ROSBAG 走行シーン分析・抽出＆検索統合プラットフォーム",
    page_icon="🚗",
    layout="wide"
)

DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"

CRITICAL_SCENARIO_TAGS = [
    "路上駐車", "対向車すれ違い", "追い越し", "割り込み", 
    "工事区間", "狭路走行", "急ブレーキ", "飛び出し注意"
]

CAMERA_MAP = {
    "camera0": "Front",
    "camera1": "Rear",
    "camera2": "FrontLeft",
    "camera3": "RearLeft",
    "camera4": "FrontRight",
    "camera5": "RearRight"
}

CAMERA_PROMPT_HINT = {

    "Front":
    """
    Front camera.

    Vehicle ahead in same direction -> 先行車

    Vehicle approaching from opposite direction -> 対向車

    Do NOT use:
    - 後続車
    """,

    "Rear":
    """
    Rear camera.

    Vehicles visible in this image are behind ego vehicle.

    Use:
    - 後続車

    Do NOT use:
    - 先行車
    - 対向車
    """,

    "FrontLeft":
    """
    Front-left camera.

    Side moving vehicle -> 並走車

    Roadside stopped vehicle -> 路上駐車
    """,

    "FrontRight":
    """
    Front-right camera.

    Side moving vehicle -> 並走車

    Roadside stopped vehicle -> 路上駐車
    """,

    "RearLeft":
    """
    Rear-left camera.

    Side moving vehicle -> 並走車

    Overtaking vehicle -> 追い越し
    """,

    "RearRight":
    """
    Rear-right camera.

    Side moving vehicle -> 並走車

    Overtaking vehicle -> 追い越し
    """
}


# ==============================================================================
# 2. Tkinter ネイティブダイアログ関数
# ==============================================================================
def open_folder_dialog():
    """OS標準のフォルダ選択ダイアログ"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        selected_path = filedialog.askdirectory(master=root)
        root.destroy()
        return selected_path
    except Exception as e:
        st.warning(f"ダイアログの起動に失敗しました（手動でパスを入力してください）: {e}")
        return ""

def open_file_dialog(filetypes=[("All files", "*.*")]):
    """OS標準のファイル選択ダイアログ"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        selected_file = filedialog.askopenfilename(master=root, filetypes=filetypes)
        root.destroy()
        return selected_file
    except Exception as e:
        st.warning(f"ダイアログの起動に失敗しました: {e}")
        return ""

# ==============================================================================
# 3. ROSBAG 画像抽出バックエンド関数
# ==============================================================================
def extract_images_from_bags(
    db3_file_paths: list,
    topic_name: str,
    output_dir: str,
    mode: str,
    interval_sec: float,
    target_count: int,
    progress_bar,
    status_text
):
    """ROSBAG (.db3) から指定ルールで画像を抽出し保存する"""
    os.makedirs(output_dir, exist_ok=True)
    typestore = get_typestore(Stores.ROS2_HUMBLE)
    
    # タイムスタンプ順にソートした Path リストを作成
    sorted_paths = [Path(p) for p in sorted(db3_file_paths)]
    
    with AnyReader(sorted_paths, default_typestore=typestore) as reader:
        connections = [x for x in reader.connections if x.topic == topic_name]
        if not connections:
            raise ValueError(f"指定されたトピック '{topic_name}' が見つかりませんでした。")

        start_time_sec = reader.start_time / 1e9
        end_time_sec = reader.end_time / 1e9
        total_duration = max(0.001, end_time_sec - start_time_sec)

        # 抽出時間間隔の決定
        if mode == "count":
            effective_interval = total_duration / max(1, target_count)
            max_limit = target_count
        else:
            effective_interval = max(0.01, interval_sec)
            max_limit = 9999999

        count = 0
        last_extracted_time = -float('inf')

        for connection, timestamp, rawdata in reader.messages(connections=connections):
            current_time = timestamp / 1e9

            if current_time - last_extracted_time >= effective_interval:
                msg = reader.deserialize(rawdata, connection.msgtype)
                
                # CompressedImage と Image (Raw) の両方に対応
                cv_image = None
                if hasattr(msg, 'format') or 'CompressedImage' in connection.msgtype:
                    np_arr = np.frombuffer(msg.data, np.uint8)
                    cv_image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                elif hasattr(msg, 'encoding') or 'Image' in connection.msgtype:
                    # Raw Image
                    img_data = np.frombuffer(msg.data, dtype=np.uint8)
                    if "bgr" in msg.encoding.lower():
                        cv_image = img_data.reshape((msg.height, msg.width, 3))
                    elif "rgb" in msg.encoding.lower():
                        raw_rgb = img_data.reshape((msg.height, msg.width, 3))
                        cv_image = cv2.cvtColor(raw_rgb, cv2.COLOR_RGB2BGR)

                if cv_image is not None:
                    #img_name = f"scene_{count:04d}_{current_time:.2f}.jpg"
                    camera_id = connection.topic.split("/")[-3]
                    camera_name = CAMERA_MAP.get(
                        camera_id,
                        camera_id
                    )
                    img_name = (
                        f"{camera_name}_scene_"
                        f"{count:04d}_"
                        f"{current_time:.2f}.jpg"
                    )
                    cv2.imwrite(os.path.join(output_dir, img_name), cv_image)
                    count += 1
                    last_extracted_time = current_time

                    # 進捗表示
                    if mode == "count":
                        progress = min(1.0, count / max(1, target_count))
                    else:
                        progress = min(1.0, max(0.0, (current_time - start_time_sec) / total_duration))
                    
                    progress_bar.progress(progress)
                    status_text.text(f"抽出中: {count} 枚完了 (現在時刻: {current_time:.2f} s / 合計: {total_duration:.1f} s)")

                    if count >= max_limit:
                        break

        progress_bar.progress(1.0)
        return count

# ==============================================================================
# 4. VLM モデルのキャッシュ読み込み＆JSONパーサー
# ==============================================================================
@st.cache_resource(show_spinner=False)
def load_vlm_pipeline(model_path):
    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    model = AutoModelForImageTextToText.from_pretrained(
        model_path,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        trust_remote_code=True
    ).to(DEVICE).eval()
    return processor, model

def parse_json_safely(text):
    clean_text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", clean_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    data = {}
    for k in ["weather", "road_type", "traffic", "risk", "potential_risk"]:
        m = re.search(rf'"{k}"\s*:\s*"([^"]*)"', text)
        if m:
            data[k] = m.group(1)
            
    elem_m = re.search(r'"elements"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if elem_m:
        data["elements"] = re.findall(r'"([^"]+)"', elem_m.group(1))
    return data

# ==============================================================================
# 5. セッションステート初期化
# ==============================================================================
if "active_csv_path" not in st.session_state:
    default_csvs = glob.glob("*4B*.csv") or glob.glob("*.csv")
    st.session_state["active_csv_path"] = os.path.abspath(default_csvs[0]) if default_csvs else ""

if "active_img_dir" not in st.session_state:
    if st.session_state["active_csv_path"]:
        st.session_state["active_img_dir"] = os.path.dirname(st.session_state["active_csv_path"])
    else:
        st.session_state["active_img_dir"] = os.path.abspath("./extracted_images")

if "bag_input_path" not in st.session_state:
    st.session_state["bag_input_path"] = os.path.abspath("./rosbags")

if "bag_output_dir" not in st.session_state:
    st.session_state["bag_output_dir"] = os.path.abspath("./extracted_images/new_batch")

# ==============================================================================
# 6. UI ヘッダーと3大タブ構成
# ==============================================================================
st.title("🚗 Autoware ROSBAG データマイニング統合プラットフォーム")
st.caption("ROSBAG画像抽出 ➔ VLM(Qwen3.5)高精度自動アノテーション ➔ セマンティック検索・シーン特定")

tab_extract, tab_annotate, tab_search = st.tabs([
    "🎥 1. ROSBAG 画像抽出 (Extract)", 
    "⚙️ 2. VLM 自動アノテーション (Tagging)", 
    "🔍 3. シーン検索・分析ビューア (Search & View)"
])

# ==============================================================================
# 🌟 TAB 1: ROSBAG 画像抽出 (複数.db3一括 / 時間間隔・均等分割)
# ==============================================================================
with tab_extract:
    st.subheader("🎥 ROSBAG (.db3) からの画像一括抽出")
    st.markdown("単一の `.db3` ファイル、または指定フォルダ配下の全分割 `.db3` を時系列順に結合して抽出し保存します。")

    col_b1, col_b2 = st.columns([1, 1])

    # 入力ソース選択
    with col_b1:
        st.markdown("##### 📥 入力 ROSBAG ソース")
        input_mode = st.radio("選択モード:", ["フォルダ単位（配下の全 .db3 を一括処理）", "単一 .db3 ファイル指定"], horizontal=True)

        col_in_btn, col_in_txt = st.columns([1, 3])
        if input_mode == "フォルダ単位（配下の全 .db3 を一括処理）":
            if col_in_btn.button("📁 フォルダ選択", help="ROSBAGフォルダを選択"):
                chosen = open_folder_dialog()
                if chosen:
                    st.session_state["bag_input_path"] = chosen
                    folder_name = os.path.basename(chosen.rstrip(os.sep))
                    st.session_state["bag_output_dir"] = os.path.abspath(os.path.join("./extracted_images", folder_name))
        else:
            if col_in_btn.button("📄 .db3 選択", help="単一の .db3 ファイルを選択"):
                chosen = open_file_dialog([("ROS2 db3 files", "*.db3"), ("All files", "*.*")])
                if chosen:
                    st.session_state["bag_input_path"] = chosen
                    base_name = Path(chosen).stem
                    st.session_state["bag_output_dir"] = os.path.abspath(os.path.join("./extracted_images", base_name))

        bag_target_path = col_in_txt.text_input("選択されたパス:", value=st.session_state["bag_input_path"])
        st.session_state["bag_input_path"] = bag_target_path

        # カメラ選択機能追加
        CAMERA_TOPICS = {
            "前方 Front": "/sensing/camera/camera0/image_rect_color/compressed",
            "後方 Rear": "/sensing/camera/camera1/image_rect_color/compressed",
            "前左 Front Left": "/sensing/camera/camera2/image_rect_color/compressed",
            "後左 Rear Left": "/sensing/camera/camera3/image_rect_color/compressed",
            "前右 Front Right": "/sensing/camera/camera4/image_rect_color/compressed",
            "後右 Rear Right": "/sensing/camera/camera5/image_rect_color/compressed"
        }

        camera_name = st.selectbox(
            "📷 カメラ選択",
            options=list(CAMERA_TOPICS.keys()),
            index=0
        )

        topic_name = CAMERA_TOPICS[camera_name]

    # 抽出ルールと出力先
    with col_b2:
        st.markdown("##### ⚙️ 抽出粒度・サンプリング設定")
        extract_mode = st.radio(
            "抽出ルール (Sampling Strategy):", 
            ["時間間隔指定 (例: ○ 秒に1枚)", "総枚数均等分割 (例: 全体から均等に ○ 枚)"]
        )

        col_p1, col_p2 = st.columns(2)
        if "時間間隔" in extract_mode:
            interval_val = col_p1.number_input("抽出間隔 (秒):", min_value=0.1, max_value=60.0, value=1.0, step=0.5)
            target_count_val = 0
            st.caption(f"💡 走行時間全体から **{interval_val} 秒おき** に1枚抽出します。")
        else:
            target_count_val = col_p1.number_input("抽出総枚数 (枚):", min_value=1, max_value=5000, value=100, step=10)
            interval_val = 1.0
            st.caption(f"💡 ROSBAG 全体の再生時間から **均等に {target_count_val} 枚** を間引き抽出します。")

        st.markdown("##### 📤 出力先フォルダ")
        col_out_btn, col_out_txt = st.columns([1, 3])
        if col_out_btn.button("📁 変更", help="保存先ディレクトリを選択"):
            chosen_out = open_folder_dialog()
            if chosen_out:
                st.session_state["bag_output_dir"] = chosen_out

        output_dir_val = col_out_txt.text_input("保存先パス:", value=st.session_state["bag_output_dir"])
        st.session_state["bag_output_dir"] = output_dir_val

    st.markdown("---")

    # 抽出実行
    if st.button("🚀 画像抽出を開始する (Extract Images)", type="primary", use_container_width=True):
        if not os.path.exists(bag_target_path):
            st.error(f"入力パスが存在しません: {bag_target_path}")
        else:
            # .db3 ファイルの収集
            if os.path.isdir(bag_target_path):
                db3_files = sorted(glob.glob(os.path.join(bag_target_path, "**", "*.db3"), recursive=True))
            else:
                db3_files = [bag_target_path] if bag_target_path.endswith(".db3") else []

            if not db3_files:
                st.error(f"指定パス内に `.db3` ファイルが見つかりませんでした: {bag_target_path}")
            else:
                st.info(f"📦 検出された .db3 ファイル数: `{len(db3_files)}` 件。画像デコードを開始します...")
                progress_bar = st.progress(0)
                status_text = st.empty()

                mode_key = "interval" if "時間間隔" in extract_mode else "count"

                try:
                    total_saved = extract_images_from_bags(
                        db3_file_paths=db3_files,
                        topic_name=topic_name,
                        output_dir=output_dir_val,
                        mode=mode_key,
                        interval_sec=interval_val,
                        target_count=target_count_val,
                        progress_bar=progress_bar,
                        status_text=status_text
                    )
                    st.success(f"🎉 抽出完了！ 合計 `{total_saved}` 枚の画像を保存しました: `{output_dir_val}`")
                    
                    # 次のタブへの自動連携
                    st.session_state["target_annotate_dir"] = output_dir_val
                    st.session_state["active_img_dir"] = output_dir_val
                    st.info("👉 そのまま「2. VLM 自動アノテーション」タブへ進むと、抽出した画像を即座に解析できます。")

                except Exception as e:
                    st.error(f"抽出処理中にエラーが発生しました: {e}")

# ==============================================================================
# 🌟 TAB 2: VLM 自動アノテーション (Qwen3.5-4B / 0.8B)
# ==============================================================================
with tab_annotate:
    st.subheader("⚙️ 抽出画像のバッチAI解析＆自動タグ付け")
    st.markdown("路上駐車・すれ違い・割り込み・工事区間などの重要走行イベントを漏れなく検出し、データベースを生成します。")

    col_f_btn, col_f_txt = st.columns([1, 4])
    if "target_annotate_dir" not in st.session_state:
        st.session_state["target_annotate_dir"] = os.path.abspath("./extracted_images")

    if col_f_btn.button("📁 フォルダ選択", help="解析したい画像フォルダを選択"):
        chosen_dir = open_folder_dialog()
        if chosen_dir:
            st.session_state["target_annotate_dir"] = chosen_dir

    target_dir = col_f_txt.text_input("解析対象画像フォルダ:", value=st.session_state["target_annotate_dir"])
    st.session_state["target_annotate_dir"] = target_dir

    local_models = [d for d in glob.glob("./models/*Qwen*") if os.path.isdir(d)]
    if not local_models:
        local_models = ["./Qwen3.5-4B", "./Qwen3.5-0.8B"]

    col_m1, col_m2 = st.columns(2)
    selected_model_path = col_m1.selectbox("使用する VLM モデル:", local_models, index=0)
    
    model_tag = "4B" if "4B" in selected_model_path or "4b" in selected_model_path else "0.8B"
    default_csv_name = f"scene_database_qwen35_{model_tag}.csv"
    output_csv_path = os.path.join(target_dir, default_csv_name)
    col_m2.text_input("保存先 CSV (自動設定):", value=output_csv_path, disabled=True)

    if st.button("🚀 アノテーション実行 (バッチ推論開始)", type="primary", use_container_width=True):
        if not os.path.exists(target_dir):
            st.error(f"フォルダが存在しません: {target_dir}")
        else:
            img_files = sorted([
                f for f in os.listdir(target_dir) 
                if f.lower().endswith(('.jpg', '.jpeg', '.png'))
            ])

            if not img_files:
                st.warning(f"画像ファイルが見つかりませんでした: {target_dir}")
            else:
                st.info(f"📸 {len(img_files)} 枚の画像を検出しました。解析を開始します...")
                
                with st.spinner("VLM モデルを GPU にロード中..."):
                    processor, model = load_vlm_pipeline(selected_model_path)
                
                progress_bar = st.progress(0)
                status_text = st.empty()

                system_prompt = "You are a headless autonomous driving data extraction engine. You output strictly raw JSON."
                user_prompt = (
                    "Extract road scene objects and specific driving interaction scenarios from this dashboard camera image.\n\n"
                    "Output in this exact JSON schema:\n"
                    "{\n"
                    '  "weather": "sunny|cloudy|rainy|night|glare",\n'
                    '  "road_type": "straight|curve|intersection|crosswalk|narrow_road",\n'
                    '  "traffic": "low|medium|high",\n'
                    '  "risk": "low|medium|high",\n'
                    '  "elements": [\n'
                    '    "画像内で【実際に目視できる要素・重要な走行状況】のみを選択して単語リストで抽出してください。",\n'
                    '    "候補タグ（存在しないものは無理に出力しないこと）: [交差点, 信号機(赤), 信号機(青), 信号機(黄), 横断歩道, 先行車, 対向車, 後続車, 並走車, 歩行者, 自転車, 路上駐車, 対向車すれ違い, 追い越し, 割り込み, 工事区間, コーン, 狭路走行, 電柱, 逆光, 停止中]"\n'
                    '  ],\n'
                    '  "potential_risk": "画像内のリアルな危険要素（特にない場合や安全停止中は必ず「特になし」）"\n'
                    "}\n\n"
                    "※ 注意: 「路上駐車」「対向車すれ違い」「割り込み」等の特殊状況は、画像内に明確に存在する場合のみタグに含めてください。"
                )

                fieldnames = [
                    "camera",
                    "image_name", 
                    "timestamp_sec", "weather", 
                    "road_type", "traffic", "risk", 
                    "elements", "potential_risk", "raw_output"
                ]

                with open(output_csv_path, mode="w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()

                    for idx, f_name in enumerate(img_files):
                        camera_name = f_name.split("_")[0]
                        camera_hint = CAMERA_PROMPT_HINT.get(
                            camera_name,
                            ""
                        )

                        status_text.text(f"解析中 ({idx+1}/{len(img_files)}): {f_name}")
                        img_full_path = os.path.join(target_dir, f_name)

                        ts_m = re.search(r"_([0-9]+\.[0-9]+)\.jpg$", f_name)
                        timestamp_sec = ts_m.group(1) if ts_m else "0.0"

                        raw_img = Image.open(img_full_path).convert("RGB")
                        w, h = raw_img.size
                        resized_img = raw_img.resize((640, int(h * (640 / w))), Image.Resampling.BILINEAR)

                        camera_aware_prompt = (
                            f"""
                            Current EGO Camera: {camera_name}

                            IMPORTANT:

                            Front camera:
                            - 先行車 = vehicle ahead of ego vehicle
                            - 対向車 = vehicle approaching ego vehicle

                            Rear camera:
                            - 後続車 = vehicle behind ego vehicle
                            - NEVER classify rear vehicles as 先行車
                            - NEVER classify rear vehicles as 対向車

                            Side camera:
                            - moving vehicle beside ego vehicle = 並走車
                            - stopped roadside vehicle = 路上駐車

                            {camera_hint}

                            {user_prompt}
                            """
                        )

                        messages = [
                            {
                                "role": "system",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": system_prompt
                                    }
                                ]
                            },

                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "image": resized_img
                                    },
                                    {
                                        "type": "text",
                                        "text": camera_aware_prompt
                                    }
                                ]
                            }
                        ]

                        prompt_text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                        prompt_text += "{\n"

                        inputs = processor(text=[prompt_text], images=[resized_img], return_tensors="pt").to(DEVICE)

                        with torch.inference_mode():
                            outputs = model.generate(**inputs, max_new_tokens=300, do_sample=False, use_cache=True)

                        prompt_len = inputs["input_ids"].shape[-1]
                        raw_body = processor.decode(outputs[0][prompt_len:], skip_special_tokens=True).strip()
                        full_json = "{\n" + raw_body

                        data = parse_json_safely(full_json)
                        raw_elems = data.get("elements", [])
                        if camera_name == "Rear":

                            raw_elems = [
                                x for x in raw_elems
                                if x not in ["先行車", "対向車"]
                            ]
                        elems_str = "、".join(raw_elems) if isinstance(raw_elems, list) else str(raw_elems)

                        record = {
                            "camera": camera_name,
                            "image_name": f_name,
                            "timestamp_sec": timestamp_sec,
                            "weather": data.get("weather", "sunny"),
                            "road_type": data.get("road_type", "straight"),
                            "traffic": data.get("traffic", "low"),
                            "risk": data.get("risk", "low"),
                            "elements": elems_str if elems_str else "通常走行",
                            "potential_risk": data.get("potential_risk", "特になし"),
                            "raw_output": full_json
                        }

                        writer.writerow(record)
                        f.flush()
                        progress_bar.progress((idx + 1) / len(img_files))

                st.success(f"🎉 全 {len(img_files)} 枚のアノテーションが完了しました！\n\n📁 保存先: `{output_csv_path}`")
                st.session_state["active_csv_path"] = output_csv_path
                st.session_state["active_img_dir"] = target_dir
                st.info("👉 「3. シーン検索・分析ビューア」タブに切り替えて、絞り込みをお試しください。")

# ==============================================================================
# 🌟 TAB 3: シーン検索・分析ビューア
# ==============================================================================
with tab_search:
    st.sidebar.header("📁 データベース読み込み")
    
    col_csv_btn, col_csv_txt = st.sidebar.columns([1, 2])
    if col_csv_btn.button("📂 選択", help="CSVファイル選択ダイアログを開きます"):
        chosen_file = open_file_dialog([("CSV files", "*.csv"), ("All files", "*.*")])
        if chosen_file:
            st.session_state["active_csv_path"] = chosen_file
            st.session_state["active_img_dir"] = os.path.dirname(chosen_file)

    current_csv = st.sidebar.text_input("CSV パス", value=st.session_state["active_csv_path"])
    if current_csv != st.session_state["active_csv_path"]:
        st.session_state["active_csv_path"] = current_csv
        st.session_state["active_img_dir"] = os.path.dirname(current_csv)

    current_img_dir = st.sidebar.text_input("画像フォルダ", value=st.session_state["active_img_dir"])
    st.session_state["active_img_dir"] = current_img_dir

    st.sidebar.markdown("---")

    if not st.session_state["active_csv_path"] or not os.path.exists(st.session_state["active_csv_path"]):
        st.info("💡 データベース (.csv) が選択されていません。左サイドバーから CSV を選択してください。")
    else:
        try:
            df = pd.read_csv(st.session_state["active_csv_path"])
            
            st.sidebar.header("🔍 シーン絞り込み")
            def get_col_opts(col):
                if col in df.columns:
                    v = list(df[col].dropna().unique())
                    return ["All"] + sorted([str(x) for x in v if str(x) not in ["ERROR", "nan", "PARSE_ERROR"]])
                return ["All"]

            sel_weather = st.sidebar.selectbox("天候 (Weather)", get_col_opts("weather"))
            sel_road = st.sidebar.selectbox("道路構造 (Road Type)", get_col_opts("road_type"))
            sel_risk = st.sidebar.selectbox("リスク度 (Risk Level)", get_col_opts("risk"))
            if "camera" in df.columns:
                sel_camera = st.sidebar.selectbox(
                    "📷 Camera",
                    ["All"] + sorted(df["camera"].dropna().unique())
                )
            else:
                sel_camera = "All"

            sel_critical = st.sidebar.selectbox(
                "⚠️ 重要イベント・問題シーン", 
                ["All"] + CRITICAL_SCENARIO_TAGS
            )

            search_kw = st.sidebar.text_input("🏷️ 要素・フリーワード検索", placeholder="例: 信号機, コーン, 路上駐車")

            filtered_df = df.copy()
            if sel_weather != "All" and "weather" in filtered_df.columns:
                filtered_df = filtered_df[filtered_df["weather"] == sel_weather]
            if sel_road != "All" and "road_type" in filtered_df.columns:
                filtered_df = filtered_df[filtered_df["road_type"] == sel_road]
            if sel_risk != "All" and "risk" in filtered_df.columns:
                filtered_df = filtered_df[filtered_df["risk"] == sel_risk]
            if sel_critical != "All":
                filtered_df = filtered_df[filtered_df["elements"].astype(str).str.contains(sel_critical, na=False)]
            if(
                sel_camera != "All"
                and "camera" in filtered_df.columns
            ):
                filtered_df = filtered_df[filtered_df["camera"] == sel_camera]

            if search_kw.strip():
                for kw in search_kw.strip().split():
                    mask = pd.Series(False, index=filtered_df.index)
                    for c in ["elements", "tags", "potential_risk", "hazard", "description", "raw_output"]:
                        if c in filtered_df.columns:
                            mask = mask | filtered_df[c].astype(str).str.contains(kw, na=False)
                    filtered_df = filtered_df[mask]

            st.markdown(f"#### 🎯 ヒット件数: `{len(filtered_df)}` 件 / 全 {len(df)} 件")

            if filtered_df.empty:
                st.warning("⚠️ 該当するシーンが見つかりませんでした。")
            else:
                cols = st.columns(3)
                for idx, (_, row) in enumerate(filtered_df.iterrows()):
                    img_name = str(row.get("image_name", ""))
                    img_path = os.path.join(st.session_state["active_img_dir"], img_name)
                    
                    with cols[idx % 3]:
                        with st.container(border=True):
                            if os.path.exists(img_path):
                                st.image(img_path, use_container_width=True)
                            else:
                                st.info(f"🖼️ [{img_name}] (画像が見つかりません)")

                            ts_val = row.get("timestamp_sec", "0.0")
                            st.markdown(f"⏱ **タイムスタンプ:** `{ts_val}` s")

                            camera_name = row.get("camera", "Unknown")

                            st.markdown(
                                f"📷 **Camera:** `{camera_name}`"
                            )

                            risk_val = str(row.get("risk", "low"))
                            risk_icon = "🔴" if risk_val == "high" else ("🟡" if risk_val == "medium" else "🟢")
                            st.markdown(f"{risk_icon} リスク: **{risk_val}** | 天候: `{row.get('weather', 'unknown')}` | 道路: `{row.get('road_type', 'unknown')}`")

                            raw_tags = str(row.get("elements", row.get("tags", "")))
                            tag_list = [t.strip() for t in re.split(r"[,、]", raw_tags) if t.strip() and t.strip() not in ["nan", "ERROR"]]
                            
                            tags_html_parts = []
                            for t in tag_list:
                                if any(crit in t for crit in CRITICAL_SCENARIO_TAGS):
                                    tags_html_parts.append(
                                        f"<span style='background-color: #5c2b14; color: #ff9f43; border: 1px solid #ff9f43; padding: 2px 8px; border-radius: 10px; font-size: 12px; margin-right: 4px; display: inline-block; margin-bottom: 4px; font-weight: bold;'>⚠️ #{t}</span>"
                                    )
                                else:
                                    tags_html_parts.append(
                                        f"<span style='background-color: #202b38; color: #4facfe; padding: 2px 8px; border-radius: 10px; font-size: 12px; margin-right: 4px; display: inline-block; margin-bottom: 4px;'>#{t}</span>"
                                    )
                            
                            tags_html = " ".join(tags_html_parts) or "<span style='color:#888;font-size:12px;'>（特筆要素なし）</span>"
                            st.markdown(f"**🏷 検出要素・走行イベント:**<br>{tags_html}", unsafe_allow_html=True)

                            risk_desc = str(row.get("potential_risk", row.get("hazard", "特になし")))
                            r_icon = "🟢" if ("なし" in risk_desc or risk_desc == "none") else "⚠️"
                            st.markdown(f"{r_icon} **潜在リスク:** `{risk_desc}`")

                            try:
                                offset = float(ts_val) % 1000
                                ros_cmd = f"ros2 bag play ./rosbags/DATA.db3 --start-offset {offset:.1f}"
                            except ValueError:
                                ros_cmd = "ros2 bag play ./rosbags/DATA.db3"
                            st.code(ros_cmd, language="bash")

                with st.expander("📊 データベース生テーブル表示 (CSV Raw View)"):
                    st.dataframe(filtered_df, use_container_width=True)

        except Exception as e:
            st.error(f"データベース読み込みエラー: {e}")