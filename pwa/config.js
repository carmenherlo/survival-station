// Survival Station — API endpoints
// Before local dev: uncomment localhost lines
// Before deploying to device: uncomment production lines
 
const CONFIG = {
  RAG_API: '/api',                            // production: via nginx
  // RAG_API: 'http://localhost:8000',        // local dev
 
  TRANSLATOR_API: 'http://10.42.0.1:5000',   // production: direct port
  // TRANSLATOR_API: 'http://localhost:5000', // local dev
};