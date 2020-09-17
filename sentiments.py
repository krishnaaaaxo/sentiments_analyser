from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
import json
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address, get_ipaddr
import boto3
from bs4 import BeautifulSoup
from itertools import islice
import tweepy as tw
from datetime import datetime, timedelta

FINWIZ_URL = "https://finviz.com/quote.ashx?t=" # + symbol
YAHOO_API_URL = "https://yahoo-finance15.p.rapidapi.com/api/yahoo/qu/quote/" # + symbol
HEADERS = {"User-Agent": "Mozilla/5"}
RAPIDAPI_HEADERS = {
    'x-rapidapi-host': os.environ["x_rapidapi_host"],
    'x-rapidapi-key': os.environ["x_rapidapi_key"]
}

TWITTER_KEY = os.environ["TWITTER_KEY"]
TWITTER_SECRET_KEY = os.environ["TWITTER_SECRET_KEY"]
TWITTER_ACCESS_TOKEN = os.environ["TWITTER_ACCESS_TOKEN"]
TWITTER_ACCESS_TOKEN_SECRET = os.environ["TWITTER_ACCESS_TOKEN_SECRET"]

auth = tw.OAuthHandler(TWITTER_KEY, TWITTER_SECRET_KEY)
auth.set_access_token(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET)
twitter_api = tw.API(auth, wait_on_rate_limit=True)

app = Flask(__name__)
CORS(app)

# AWS COMPREHEND SETUP
COMPREHEND = boto3.client(service_name="comprehend", region_name="us-east-1")

# Rate limiter to prevent abuse
limiter = Limiter(app, key_func=get_ipaddr, default_limits=["10 per minute", "100 per hour", "300 per day"])

@app.errorhandler(404)
def errorHandler(e):
    return jsonify({"type": "error", "message": "Address not found."}), 404

@app.errorhandler(405)
def errorHandler(e):
    return jsonify({"type": "error", "message": "The method is not allowed for the requested URL."}), 405

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"type": "error", "status": 429, "message": "Rate limit exceeded %s" % e.description}), 429

@app.route("/sentiments/symbol/get_information", methods=["POST"])
def getSymbolInformation():
    try:
        data = request.get_json()
        symbol = data["symbol"]

        data_url = YAHOO_API_URL + symbol + "/financial-data"
        profile_url = YAHOO_API_URL + symbol + "/asset-profile"

        info = requests.get(data_url, headers=RAPIDAPI_HEADERS)
        profile = requests.get(profile_url, headers=RAPIDAPI_HEADERS)

        return jsonify({"type": "success", "information": info.json(), "profile": profile.json()}), 200
    except Exception as e:
        return jsonify({"type": "error", "message": str(e)}), 500

@app.route("/sentiments/symbol/get_recommendation", methods=["POST"])
def getSymbolRecommendation():
    try:
        data = request.get_json()
        symbol = data["symbol"]

        weightages = {"strongBuy": 0.2, "buy": 0.4, "hold": 0.6, "sell": 0.8, "strongSell": 1}

        api_url = YAHOO_API_URL + symbol + "/recommendation-trend"

        response = requests.get(api_url, headers=RAPIDAPI_HEADERS).json()

        trends = response["recommendationTrend"]["trend"][0]

        del trends["period"]

        results = 0
        total = sum(trends.values())

        for recos in weightages:
            weight = weightages[recos] * (trends[recos] / total)
            results += weight

        return jsonify({"type": "success", "recommendation_trend": trends, "recommendation": results}), 200
    except Exception as e:
        return jsonify({"type": "error", "message": str(e)}), 500

@app.route("/sentiments/symbol/get_news", methods=["POST"])
def getSymbolNews():
    try:
        data = request.get_json()
        symbol = data["symbol"]
        tweets = []

        new_search = symbol + ' "#' + symbol + '" -filter:retweets'

        tweets = tw.Cursor(twitter_api.search,
                   q=new_search,
                   lang="en",
                   result_type="recent",
                   since=(datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')).items(10)

        all_tweets = []
        for tweet in tweets:
            all_tweets.append("@" + tweet.user.screen_name + ": " + tweet.text)

        source = requests.get(FINWIZ_URL + symbol, headers=HEADERS).text
        html = BeautifulSoup(source, "html.parser")

        news_table = html.find(id="news-table")
        news = {}

        row_news_table = news_table.findAll("tr")

        # Capped to a limit of 10
        for x in islice(row_news_table, 0, 10):
            
            text = x.a.get_text()
            url = x.a.get("href")

            news[text] = url

    except Exception as e:
        return jsonify({"type": "error", "message": str(e)}), 500

    return jsonify({"type": "success", "fetched_news": news, "tweets": all_tweets}), 200

@app.route("/sentiments/symbol/get_sentiment", methods=["POST"])
def getSentiment():
    sentiment_count = {"NEUTRAL": 0, "POSITIVE": 0, "NEGATIVE": 0}
    key_phrases = []

    try:
        data = request.get_json()
        fetched_news = data["fetched_news"]

        for news in fetched_news:
            sentiment = COMPREHEND.detect_sentiment(Text=news, LanguageCode="en")
            keyword_list = COMPREHEND.detect_key_phrases(Text=news, LanguageCode="en")["KeyPhrases"]
            for keywords in keyword_list:
                # Get rid of noise keywords, only accept those above 3 words
                if len(keywords["Text"].split()) > 3:
                    key_phrases.append(keywords["Text"])
            sentiment_count[sentiment["Sentiment"]] += 1

        return jsonify({"type": "success", "key_phrases": key_phrases, "overall_sentiments": max(sentiment_count, key=sentiment_count.get), "sentiments": sentiment_count}), 200
    except Exception as e:
        return jsonify({"type": "error", "message": str(e)}), 500

# Rename of .py files easily
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9000, debug=True)