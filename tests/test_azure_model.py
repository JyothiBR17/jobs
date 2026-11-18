"""Verify Azure OpenAI uses the dedicated UK South endpoint and real schema."""
import json
import unittest
from unittest.mock import patch

import httpx

from app.agents import Profile, analyse


PROFILE = Profile.model_validate({
    "name": "Test Candidate", "summary": "Azure architecture",
    "facts": [{"text":"Implemented Azure Policy","category":"security","employer":"Example"}],
    "notice_days": 30, "source_verified": True,
})
JOB = {"title":"Azure Cloud Engineer","description":"Azure Policy security",
       "location":"London, United Kingdom"}


class AzureModelTests(unittest.TestCase):
    @patch("app.agents.get_ai_backend", return_value={
        "provider": "azure_openai",
        "key": "secret-that-is-never-logged",
        "endpoint": "https://jobsaiuksouth17openai.openai.azure.com/",
        "deployment": "jobhunter-chat",
    })
    def test_azure_chat_request_uses_region_scoped_endpoint(self, _backend):
        fake_result = {
            "architecture":{"requirement":"","fact_index":-1,"points":0},
            "automation":{"requirement":"","fact_index":-1,"points":0},
            "security":{"requirement":"Azure Policy","fact_index":0,"points":100},
            "experience":{"requirement":"","fact_index":-1,"points":0},
            "location_compensation":{"requirement":"","fact_index":-1,"points":0},
            "seniority":"unknown", "experience_years":None,
            "salary":None, "work_arrangement":None, "deadline":None,
            "preferred_profile":"security",
        }
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual("secret-that-is-never-logged", request.headers["api-key"])
            self.assertNotIn("authorization", request.headers)
            self.assertEqual("jobsaiuksouth17openai.openai.azure.com", request.url.host)
            self.assertEqual("/openai/deployments/jobhunter-chat/chat/completions", request.url.path)
            payload=json.loads(request.content)
            self.assertEqual("json_schema", payload["response_format"]["type"])
            self.assertEqual("jobhunter-chat", payload["model"])
            return httpx.Response(200, json={
                "choices":[{"message":{"content":json.dumps(fake_result)}}],
                "usage":{"prompt_tokens":50,"completion_tokens":80}
            })
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("app.agents.httpx.Client",return_value=client):
            result, usage = analyse(JOB, PROFILE)
        self.assertEqual(100, result.security.points)
        self.assertEqual(50, usage["prompt_tokens"])
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
