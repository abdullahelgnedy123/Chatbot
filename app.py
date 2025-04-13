from flask import Flask, render_template, request, jsonify
from chat import get_response
from deep_translator import GoogleTranslator
import langdetect
import requests
from duckduckgo_search import DDGS
import wikipedia
import google.generativeai as genai
import os
import json
from dotenv import load_dotenv
from difflib import get_close_matches

# تحميل مفتاح API من ملف .env
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ لم يتم العثور على مفتاح Gemini API، تأكد من إضافته في ملف .env!")

genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)

# تحميل البيانات من ملف JSON أو إنشاء ملف جديد إذا لم يكن موجودًا
QUESTIONS_FILE = "questions.json"
INTENTS_FILE = "intents.json"
try:
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        questions_data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    questions_data = {}

try:
    with open(INTENTS_FILE, "r", encoding="utf-8") as f:
        intents_data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    intents_data = {"intents": []}

def is_valid_question(question):
    """يتحقق مما إذا كان السؤال منطقيًا ليتم تخزينه"""
    return len(question) > 2 and not question.isdigit()

def save_question(question, answer):
    """يحفظ السؤال والإجابة في ملف JSON إذا كان السؤال منطقيًا والإجابة ذات معنى"""
    if is_valid_question(question) and answer not in ["❌ لم أتمكن من العثور على إجابة.", "مع السلامة", "إلى اللقاء", "سلام"]:
        questions_data[question] = answer
        with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(questions_data, f, ensure_ascii=False, indent=4)

@app.route('/')
def home():
    return render_template('base.html')

# متغير لتخزين آخر tag تم التعرف عليه
last_user_tag = None

# دالة fuzzy matching
def fuzzy_match_question(question, data):
    """يبحث عن أقرب تطابق للأسئلة"""
    all_keys = list(data.keys())
    matches = get_close_matches(question.lower(), all_keys, n=1, cutoff=0.6)
    return data[matches[0]] if matches else None

def search_local_questions(query):
    """يبحث في القاموس عن إجابة"""
    return fuzzy_match_question(query, questions_data)

def search_intents(question):
    """يبحث في intents.json باستخدام fuzzy matching"""
    for intent in intents_data.get("intents", []):
        patterns = intent.get("patterns", [])
        match = get_close_matches(question, patterns, n=1, cutoff=0.6)
        if match:
            return intent.get("responses", [])[0], intent.get("tag")
    return None, None

def search_wikipedia(query):
    """يبحث في ويكيبيديا عن ملخص قصير حول السؤال"""
    try:
        wikipedia.set_lang("ar")  # تعيين اللغة للعربية
        summary = wikipedia.summary(query, sentences=2)
        return summary if query.lower() in summary.lower() else None
    except (wikipedia.exceptions.PageError, wikipedia.exceptions.DisambiguationError):
        return None

def search_gemini(query):
    """يبحث في Google Gemini API مع التحقق من الحد اليومي"""
    try:
        print(f"🌍 البحث في Google Gemini عن: {query}")
        model = genai.GenerativeModel("gemini-pro")
        response = model.generate_content(f"أجب بإيجاز حول: {query}")
        return response.text if response.text and query.lower() in response.text.lower() else None
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث في Gemini: {e}")
        return None

def search_duckduckgo(query):
    """يبحث في DuckDuckGo ويعيد المعلومات النصية بدلاً من الرابط"""
    try:
        print(f"🔍 جاري البحث عن: {query}")
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        return results[0]['body'] if results else None
    except Exception as e:
        print(f"⚠️ خطأ أثناء البحث: {e}")
        return None

# توسيع كلمات المفتاح المرتبطة بالمتحف المصري
def is_question_about_museum(question):
    keywords = ["المتحف", "الفرعونية", "الآثار", "توت عنخ آمون", "رمسيس", "الجيزة", "الأهرامات", "الملكة حتشبسوت", "التمثال", "قاعة", "معرض"]
    return any(word in question.lower() for word in keywords)

@app.route('/get_response', methods=['POST'])
def get_bot_response():
    try:
        data = request.get_json(force=True)
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({"response": "❌ لم يتم استقبال رسالة صالحة!"}), 400

        detected_lang = langdetect.detect(user_message)
        translated_message = GoogleTranslator(source='auto', target='ar').translate(user_message) if detected_lang != "ar" else user_message

        if not is_valid_question(translated_message):
            return jsonify({"response": "❌ السؤال غير واضح، حاول إعادة صياغته!"})

        # البحث أولًا في intents للحصول على الرد والعلامة (tag)
        response, tag = search_intents(translated_message)
        bot_response = response or search_local_questions(translated_message)

        # التحقق من وجود طلب لشرح تفصيلي بناءً على كلمات مفتاحية
        detail_keywords = ["شرح", "تفصيل", "تفصيلي", "معلومات أكتر", "قل لي المزيد", "وضح أكتر", "مزيد من التفاصيل", "expand", "tell me more", "عاوز أعرف أكتر"]
        if any(word in translated_message.lower() for word in detail_keywords) and last_user_tag:
            for intent in intents_data.get("intents", []):
                if intent.get("tag") == last_user_tag and "detailed_response" in intent:
                    bot_response = intent["detailed_response"]

        if not bot_response or bot_response.lower() in ["i do not understand...", "❌ لم أتمكن من العثور على إجابة."]:
            if is_question_about_museum(translated_message):
                bot_response = search_wikipedia(translated_message) or search_gemini(translated_message) or search_duckduckgo(translated_message) or "❌ لم أتمكن من العثور على إجابة."
            else:
                bot_response = "❌ هذا السؤال خارج نطاق تخصصي، أنا هنا لمساعدتك فيما يخص المتحف المصري فقط."

            save_question(translated_message, bot_response)

        final_response = bot_response if detected_lang == "ar" else GoogleTranslator(source="ar", target=detected_lang).translate(bot_response)
        return jsonify({"response": final_response})

    except Exception as e:
        print(f"❌ خطأ في السيرفر: {e}")
        return jsonify({"response": "❌ حدث خطأ في النظام، حاول لاحقًا."}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
