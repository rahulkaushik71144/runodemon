import json
import google.generativeai as genai
from pymongo import MongoClient
import time


class NLToMongoDBQuerySystem:
    def __init__(self):
        """Initialize the Natural Language to MongoDB Query System with hardcoded credentials"""
        
        # CONFIGURE THESE VALUES
       # CONFIGURE THESE VALUES
        API_KEY = "AIzaSyCYsPGc7kXhDteLjmkn2sXrk15q7nevHeY"  # Replace with your actual Gemini API key
        MONGODB_URI = "mongodb+srv://rahulkaushik71144:rahul%4071144@cluster0.21okoyt.mongodb.net/whatsapp"  # Replace with your MongoDB connection string
        DB_NAME = "whatsapp"  # Replace with your database name
        COLLECTION_NAME = "messages"  # Replace with your collection name  # Replace with your collection name
        
        # Configure the Gemini API
        genai.configure(api_key=API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        
        # Set up MongoDB connection
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client[DB_NAME]
        self.collection = self.db[COLLECTION_NAME]
        
        # Initialize query history
        self.query_history = []
        
    def get_collection_schema(self):
        """Get a sample document from the collection to understand schema"""
        schema_sample = list(self.collection.find().limit(1))
        return json.dumps(schema_sample[0] if schema_sample else {}, default=str)

    def get_collection_fields(self):
        """Get all field names from the collection"""
        pipeline = [
            {"$project": {"arrayofkeyvalue": {"$objectToArray": "$$ROOT"}}},
            {"$unwind": "$arrayofkeyvalue"},
            {"$group": {"_id": None, "fields": {"$addToSet": "$arrayofkeyvalue.k"}}}
        ]
        result = list(self.collection.aggregate(pipeline))
        if result:
            fields = result[0]["fields"]
            return ", ".join(fields)
        return ""

    def natural_language_to_query(self, natural_language_query, query_type="auto"):
        """Convert natural language to MongoDB query using Google Gemini"""
        
        # Get collection schema information
        schema_str = self.get_collection_schema()
        fields = self.get_collection_fields()
        
        # Add examples from successful query history
        history_examples = ""
        if self.query_history:
            history_examples = "Recent successful queries:\n"
            for idx, (nl_query, mongo_query) in enumerate(self.query_history[-3:]):
                history_examples += f"- \"{nl_query}\" → {json.dumps(mongo_query)}\n"
        
        # Determine query type guidance
        query_type_guidance = ""
        if query_type == "find":
            query_type_guidance = "Return a find filter as a JSON object."
        elif query_type == "aggregate":
            query_type_guidance = "Return an aggregation pipeline as a JSON array."
        else:  # auto
            query_type_guidance = ("If the query requires aggregation (like sorting, grouping, or calculations), "
                                  "return an aggregation pipeline as a JSON array. "
                                  "If it's a simple query, return a find filter as a JSON object.")
        
        # Craft the prompt
        prompt = f"""
        Convert the following natural language query to a MongoDB query.
        Respond with valid JSON only, with no additional text or explanations.
        
        {query_type_guidance}
        
        Collection fields: {fields}
        
        Sample document from the collection:
        {schema_str}
        
        {history_examples}
        
        Standard examples:
        - "Find all users from New York" → {{"address.city": "New York"}}
        - "Show products with price less than 50" → {{"price": {{"$lt": 50}}}}
        - "Count documents by category" → [{{"$group": {{"_id": "$category", "count": {{"$sum": 1}}}}}}]
        - "Find users who ordered more than 5 items" → {{"order_count": {{"$gt": 5}}}}
        
        
        Natural language query: {natural_language_query}
        """
        
        # Generate the MongoDB query
        response = self.model.generate_content(prompt)
        
        # Parse the generated query
        try:
            # Extract the JSON content from the response
            response_text = response.text
            # Remove any potential markdown code block formatting
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            # Clean up any leading/trailing whitespace or quotes
            response_text = response_text.strip().strip('"\'')
                
            query_json = json.loads(response_text)
            
            # Store successful query for future reference
            self.query_history.append((natural_language_query, query_json))
            if len(self.query_history) > 10:  # Keep only the 10 most recent
                self.query_history.pop(0)
                
            return query_json
        except json.JSONDecodeError as e:
            return {"error": f"Failed to generate valid MongoDB query: {str(e)}", "raw_response": response.text}

    def validate_query(self, query):
        """Validate the query to ensure it's safe to execute"""
        # Check for potentially dangerous operations
        query_str = str(query)
        
        # Check for JavaScript execution which can be dangerous
        risky_operations = ["$where", "$function", "$accumulator", "$eval"]
        for op in risky_operations:
            if op in query_str:
                return False, f"Query contains potentially unsafe operation: {op}"
        
        # Check for excessive document scans (if possible)
        if isinstance(query, dict) and not any(key.startswith("$") for key in query.keys()):
            # This is a find query, we can check if it has an index
            indexes = list(self.collection.list_indexes())
            has_useful_index = False
            
            for index in indexes:
                index_keys = set(index['key'].keys())
                query_keys = set(query.keys())
                if len(index_keys.intersection(query_keys)) > 0:
                    has_useful_index = True
                    break
            
            if not has_useful_index and self.collection.count_documents({}) > 10000:
                return False, "Query might be inefficient (no useful index found for a large collection)"
        
        return True, "Query is valid"

    def execute_query(self, query, limit=100):
        """Execute the generated MongoDB query"""
        is_valid, message = self.validate_query(query)
        if not is_valid:
            return {"error": message}
            
        try:
            start_time = time.time()
            
            # Handle different query types
            if isinstance(query, list):
                # This is an aggregation pipeline
                results = list(self.collection.aggregate(query, allowDiskUse=True))
            else:
                # This is a find query
                results = list(self.collection.find(query).limit(limit))
            
            execution_time = time.time() - start_time
            
            # Convert ObjectId to string for JSON serialization
            results_serializable = json.loads(json.dumps(results, default=str))
            
            return {
                "results": results_serializable,
                "count": len(results),
                "execution_time_ms": round(execution_time * 1000, 2),
                "query_type": "aggregation" if isinstance(query, list) else "find"
            }
        except Exception as e:
            return {"error": str(e)}
    

    def process_query(self, natural_language_query, include_explanation=True):
        """Process a natural language query and return results with optional explanation"""
        # Check for query type directives
        query_type = "auto"
        if natural_language_query.lower().startswith("find:"):
            query_type = "find"
            natural_language_query = natural_language_query[5:].strip()
        elif natural_language_query.lower().startswith("aggregate:"):
            query_type = "aggregate"
            natural_language_query = natural_language_query[10:].strip()
        
        # Convert to MongoDB query
        mongo_query = self.natural_language_to_query(natural_language_query, query_type)
        
        if "error" in mongo_query:
            return {
                "status": "error",
                "message": mongo_query["error"],
                "raw_response": mongo_query.get("raw_response", "")
            }
            
        # Execute the query
        results = self.execute_query(mongo_query)
        
        response = {
            "status": "success" if "error" not in results else "error",
            "generated_query": mongo_query,
        }
        
        if "error" in results:
            response["message"] = results["error"]
        else:
            response.update({
                "results": results["results"],
                "count": results["count"],
                "execution_time_ms": results["execution_time_ms"],
                "query_type": results["query_type"]
            })
            
            # Generate explanation if requested
            if include_explanation:
                explanation = self.generate_query_explanation(
                    natural_language_query, 
                    mongo_query, 
                    results
                )
                response["explanation"] = explanation
        
        return response
    

    def generate_query_explanation(self, natural_language_query, mongo_query, results):
        """Generate a natural language explanation of the query and results"""
        
        result_count = len(results["results"]) if "results" in results else 0
        result_sample = results["results"][:3] if "results" in results else []
        
        prompt = f"""
        
        Give a clear natural language result of what the result is showing 
        
       
        
        
        
        Number of results: {result_count}
        
        Sample results (first 3): {json.dumps(result_sample, default=str)}
        
        Provide a conversational explanation of:
      
         What the results shows, make it as short as possible, its like the user is talking to the database itself, and its telling in natural langauge
        
        
        .
        """
        
        # Generate the explanation
        response = self.model.generate_content(prompt)
        return response.text