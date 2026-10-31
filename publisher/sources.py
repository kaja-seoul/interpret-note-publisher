"""수집기 소스 목록 v1 (리서치 2026-10-31 sources.md '매일 05시 수집기가 먼저 긁을 15개' 기준).

type: rss(RSS/Atom) · appstore(iTunes lookup JSON) · korean(한국 중복 확인용, 후보로는 안 넣음)
kind: 수집함 '종류' 칸 값. 바꾸거나 더하려면 이 파일만 고친다. 유튜브 채널은 https://www.youtube.com/feeds/videos.xml?channel_id=<ID>.
주의: RSSHub 공개 인스턴스와 Reddit은 클라우드에서 막힐 수 있어 넣지 않았다(실패해도 나머지는 돈다).
"""

# 잡다한 매체 피드에서 남길 제목(대소문자 무시). 딜·리뷰·드라마는 빠지고 AI·설정·기능·방법만 남는다
TOPIC = r"AI|ChatGPT|GPT|Gemini|Claude|Siri|Intelligence|Copilot|Perplexity|NotebookLM|Assistant|Grok|Llama|model|prompt|how to|tip|setting|feature|hidden|free|update|beta|iOS|macOS|Android|Workspace|Gmail|Docs|Sheets|Photos|Maps|Translate|WhatsApp|Chrome"


SOURCES = [
    # ① 공식 릴리스·발표 (바로 쓸 것이 가장 많이 나오는 곳)
    {"name": "OpenAI 뉴스", "url": "https://openai.com/news/rss.xml", "type": "rss", "kind": "공식 발표"},
    {"name": "Anthropic 뉴스", "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml", "type": "rss", "kind": "공식 발표"},
    {"name": "Google Gemini 블로그", "url": "https://blog.google/products-and-platforms/products/gemini/rss/", "type": "rss", "kind": "공식 발표"},
    {"name": "Google Workspace 업데이트(Gemini)", "url": "https://workspaceupdates.googleblog.com/feeds/posts/default/-/Gemini", "type": "rss", "kind": "릴리스 노트"},
    {"name": "App Store KR · ChatGPT", "url": "https://itunes.apple.com/lookup?id=6448311069&country=kr", "type": "appstore", "kind": "앱 업데이트"},
    {"name": "App Store KR · Claude", "url": "https://itunes.apple.com/lookup?id=6473753684&country=kr", "type": "appstore", "kind": "앱 업데이트"},
    {"name": "Apple 뉴스룸", "url": "https://www.apple.com/newsroom/rss-feed.rss", "type": "rss", "kind": "공식 발표"},
    # ② 설정·숨은 기능을 빨리 쓰는 매체
    {"name": "9to5Google", "url": "https://9to5google.com/feed/", "type": "rss", "kind": "매체", "filter": TOPIC},
    {"name": "9to5Mac", "url": "https://9to5mac.com/feed/", "type": "rss", "kind": "매체", "filter": TOPIC},
    {"name": "TestingCatalog", "url": "https://www.testingcatalog.com/rss/", "type": "rss", "kind": "매체"},
    # ③ 실무자 팁·뉴스레터
    {"name": "The Rundown AI", "url": "https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml", "type": "rss", "kind": "뉴스레터"},
    {"name": "TLDR AI", "url": "https://tldr.tech/api/rss/ai", "type": "rss", "kind": "뉴스레터"},
    {"name": "Simon Willison (도구)", "url": "https://simonwillison.net/atom/beats/tool/", "type": "rss", "kind": "실무자 팁"},
    {"name": "One Useful Thing", "url": "https://www.oneusefulthing.org/feed", "type": "rss", "kind": "실무자 팁"},
    {"name": "Karpathy 블로그", "url": "https://karpathy.bearblog.dev/feed/", "type": "rss", "kind": "실무자 팁"},
    # ④ 커뮤니티 상위 글
    {"name": "Hacker News 150점+", "url": "https://hnrss.org/newest?points=150&q=ChatGPT+OR+Gemini+OR+Claude+OR+iPhone+OR+Mac", "type": "rss", "kind": "커뮤니티"},
    {"name": "Hacker News Show 100점+", "url": "https://hnrss.org/show?points=100", "type": "rss", "kind": "커뮤니티", "filter": TOPIC},
    # ④-2 유튜브 (채널 RSS. 영상 제목·설명만 온다. 자막은 안 읽는다)
    {"name": "YouTube · The AI Advantage", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCHhYXsLBEVVnbvsq57n1MTQ", "type": "rss", "kind": "유튜브"},
    {"name": "YouTube · Nate B Jones", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC0C-17n9iuUQPylguM1d-lQ", "type": "rss", "kind": "유튜브"},
    {"name": "YouTube · Futurepedia", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC_RovKmk0OCbuZjA8f08opw", "type": "rss", "kind": "유튜브"},
    {"name": "YouTube · Tina Huang", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC2UXDak6o7rBm23k3Vv5dww", "type": "rss", "kind": "유튜브"},
    {"name": "YouTube · OpenAI", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXZCJLdBC09xxGZ6gcdrc6A", "type": "rss", "kind": "유튜브"},
    # ⑤ 한국 중복 확인용 (후보 아님)
    {"name": "YouTube · 조코딩", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCQNE2JmbasNYbjGAcuBiRRg", "type": "korean", "kind": "한국"},
    {"name": "GeekNews", "url": "https://news.hada.io/rss/news", "type": "korean", "kind": "한국"},
    {"name": "AI타임스", "url": "https://www.aitimes.com/rss/allArticle.xml", "type": "korean", "kind": "한국"},
]
