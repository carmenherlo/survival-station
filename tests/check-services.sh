# ==========================================
# SERVICE CHECKLIST
# ==========================================

# 1. OLLAMA  - Does it respond to a real query?
echo -e "\n\e[1;33m>>> 1. CHECKING OLLAMA (phi3:mini)...\e[0m"
curl http://localhost:11434/api/generate -d '{
  "model": "phi3:mini",
  "prompt": "say hello",
  "stream": false
}' | jq '{response: .response, tokens: .eval_count, speed_ns: .eval_duration}'
echo ""

# 2. RAG API - does it answer a question?
echo -e "\e[1;33m>>> 2. CHECKING RAG API...\e[0m"
curl http://localhost:8000/query -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "test"}' | jq
echo ""

# 3. KIWIX - does it have the zim loaded?
echo -e "\e[1;33m>>> 3. CHECKING KIWIX CATALOG...\e[0m"
curl -s http://localhost:8888/catalog/v2/entries | grep -oP '(?<=<title>).*?(?=</title>)'
echo ""

# 4. TILESERVER - does it serve tiles?
echo -e "\e[1;33m>>> 4. CHECKING TILESERVER HEALTH...\e[0m"
curl http://localhost:8080/health
echo ""

# 5. PWA - does nginx serve the app?
echo -e "\e[1;33m>>> 5. CHECKING PWA (nginx)...\e[0m"
echo -n "HTML principal:  " && curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:80
echo -n "Manifest:        " && curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:80/manifest.json
echo -n "Service Worker:  " && curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:80/sw.js
echo ""

# 5b. PWA proxy - does /api/ reach rag-api through nginx?
echo -e "\e[1;33m>>> 5.b. CHECKING PWA → API PROXY...\e[0m"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "test"}' http://localhost:80/api/query
echo ""

echo -e "\e[1;32m——— ALL TESTS FINISHED ———\e[0m"
echo ""
