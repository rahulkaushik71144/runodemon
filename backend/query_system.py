import json
import google.generativeai as genai
from pymongo import MongoClient
import time


class NLToMongoDBQuerySystem:
    def __init__(self):
        """Initialize the Natural Language to MongoDB Query System with hardcoded credentials"""
        
        # CONFIGURE THESE VALUES
        API_KEY = "AIzaSyCYsPGc7kXhDteLjmkn2sXrk15q7nevHeY"  # Replace with your actual Gemini API key
        MONGODB_URI = "mongodb+srv://rahulkaushik71144:rahul%4071144@cluster0.21okoyt.mongodb.net/whatsapp"  # Replace with your MongoDB connection string
        DB_NAME = "whatsapp"  # Replace with your database name
        COLLECTION_NAME = "messages"  # Replace with your collection name
        
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
        
        # Enhanced query type guidance to support more operations
        query_type_guidance = """
Return a valid MongoDB query in JSON format based on the intent of the natural language query:

- **Find queries**: If the query is about retrieving/searching data, return a `find` query as a JSON object. Example: {"name": "John"}
- **Aggregation queries**: If the query requires grouping, sorting, calculations, or complex analysis, return an aggregation pipeline as a JSON array. Example: [{"$group": {"_id": "$category", "count": {"$sum": 1}}}]
- **Insert queries**: If the query asks to add/create new document(s), return an `insertOne` or `insertMany` operation. Example: {"insertOne": {"document": {"name": "John", "age": 30}}}
- **Update queries**: If the query requires modifying/changing existing data, return an `updateOne` or `updateMany` operation. Example: {"updateOne": {"filter": {"name": "John"}, "update": {"$set": {"age": 31}}}}
- **Delete queries**: If the query requires removing/deleting data, return a `deleteOne` or `deleteMany` operation. Example: {"deleteOne": {"filter": {"name": "John"}}}
- **Count queries**: If the query asks "how many" or needs to count documents, return a `countDocuments` query. Example: {"countDocuments": {"category": "electronics"}}
- **Distinct queries**: If the query needs unique values, return a `distinct` operation. Example: {"distinct": {"field": "category"}}
- **Complex queries**: For complex needs, return the appropriate combination of operations.

Respond **only with JSON** and no explanations.
"""
        
        # Craft the prompt with expanded examples
        prompt = f"""
Convert the following natural language query to a MongoDB query.
Respond with valid JSON only, with no additional text or explanations.

{query_type_guidance}

Collection fields: {fields}

Sample document from the collection:
{schema_str}

{history_examples}

Standard examples:
- "Find all users from New York" → {{"find": {{"address.city": "New York"}}}}
- "Show products with price less than 50" → {{"find": {{"price": {{"$lt": 50}}}}}}
- "Count documents by category" → [{{"$group": {{"_id": "$category", "count": {{"$sum": 1}}}}}}]
- "Find users who ordered more than 5 items" → {{"find": {{"order_count": {{"$gt": 5}}}}}}
- "Add a new user named John with age 30" → {{"insertOne": {{"document": {{"name": "John", "age": 30}}}}}}
- "Update John's age to 31" → {{"updateOne": {{"filter": {{"name": "John"}}, "update": {{"$set": {{"age": 31}}}}}}}}
- "Delete all products with zero inventory" → {{"deleteMany": {{"filter": {{"inventory": 0}}}}}}
- "Get unique categories of all products" → {{"distinct": {{"field": "category"}}}}

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
        
        # Check for excessive document scans for find operations
        if isinstance(query, dict):
            operation_type = self._get_operation_type(query)
            
            if operation_type == "find":
                filter_query = query.get("find", query)  # Handle both new and old format
                indexes = list(self.collection.list_indexes())
                has_useful_index = False
                
                for index in indexes:
                    index_keys = set(index['key'].keys())
                    query_keys = set(filter_query.keys())
                    if len(index_keys.intersection(query_keys)) > 0:
                        has_useful_index = True
                        break
                
                if not has_useful_index and self.collection.count_documents({}) > 10000:
                    return False, "Query might be inefficient (no useful index found for a large collection)"
        
        return True, "Query is valid"

    def _get_operation_type(self, query):
        """Determine the type of MongoDB operation from the query"""
        if isinstance(query, list):
            return "aggregate"
        
        # Check for operation keys in the new structured format
        for op_type in ["find", "insertOne", "insertMany", "updateOne", "updateMany", 
                        "deleteOne", "deleteMany", "countDocuments", "distinct"]:
            if op_type in query:
                return op_type
                
        # Default to find for backwards compatibility
        return "find"

    def execute_query(self, query, limit=100):
        """Execute the generated MongoDB query with support for multiple operations"""
        is_valid, message = self.validate_query(query)
        if not is_valid:
            return {"error": message}
            
        try:
            start_time = time.time()
            operation_type = self._get_operation_type(query)
            results = None
            
            # Handle different query types
            if operation_type == "aggregate":
                # Aggregation pipeline
                pipeline = query if isinstance(query, list) else query.get("aggregate", [])
                results = list(self.collection.aggregate(pipeline, allowDiskUse=True))
                
            elif operation_type == "find":
                # Find operation
                filter_query = query.get("find", query)  # Handle both new and old format
                projection = query.get("projection", None)
                sort = query.get("sort", None)
                skip = query.get("skip", 0)
                limit = query.get("limit", limit)
                
                cursor = self.collection.find(filter_query, projection)
                if sort:
                    cursor = cursor.sort(sort)
                if skip:
                    cursor = cursor.skip(skip)
                if limit:
                    cursor = cursor.limit(limit)
                    
                results = list(cursor)
                
            elif operation_type == "insertOne":
                # Insert one document
                document = query.get("insertOne", {}).get("document", {})
                result = self.collection.insert_one(document)
                results = [{"inserted_id": str(result.inserted_id), "acknowledged": result.acknowledged}]
                
            elif operation_type == "insertMany":
                # Insert multiple documents
                documents = query.get("insertMany", {}).get("documents", [])
                result = self.collection.insert_many(documents)
                results = [{
                    "inserted_ids": [str(id) for id in result.inserted_ids],
                    "acknowledged": result.acknowledged,
                    "inserted_count": len(result.inserted_ids)
                }]
                
            elif operation_type == "updateOne":
                # Update one document
                filter_query = query.get("updateOne", {}).get("filter", {})
                update = query.get("updateOne", {}).get("update", {})
                upsert = query.get("updateOne", {}).get("upsert", False)
                
                result = self.collection.update_one(filter_query, update, upsert=upsert)
                results = [{
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None,
                    "acknowledged": result.acknowledged
                }]
                
            elif operation_type == "updateMany":
                # Update multiple documents
                filter_query = query.get("updateMany", {}).get("filter", {})
                update = query.get("updateMany", {}).get("update", {})
                upsert = query.get("updateMany", {}).get("upsert", False)
                
                result = self.collection.update_many(filter_query, update, upsert=upsert)
                results = [{
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None,
                    "acknowledged": result.acknowledged
                }]
                
            elif operation_type == "deleteOne":
                # Delete one document
                filter_query = query.get("deleteOne", {}).get("filter", {})
                result = self.collection.delete_one(filter_query)
                results = [{
                    "deleted_count": result.deleted_count,
                    "acknowledged": result.acknowledged
                }]
                
            elif operation_type == "deleteMany":
                # Delete multiple documents
                filter_query = query.get("deleteMany", {}).get("filter", {})
                result = self.collection.delete_many(filter_query)
                results = [{
                    "deleted_count": result.deleted_count,
                    "acknowledged": result.acknowledged
                }]
                
            elif operation_type == "countDocuments":
                # Count documents
                filter_query = query.get("countDocuments", {})
                count = self.collection.count_documents(filter_query)
                results = [{"count": count}]
                
            elif operation_type == "distinct":
                # Get distinct values
                field = query.get("distinct", {}).get("field", "")
                filter_query = query.get("distinct", {}).get("filter", {})
                distinct_values = self.collection.distinct(field, filter_query)
                results = [{"field": field, "values": distinct_values}]
            
            execution_time = time.time() - start_time
            
            # Convert ObjectId to string for JSON serialization
            results_serializable = json.loads(json.dumps(results, default=str))
            
            return {
                "results": results_serializable,
                "count": len(results),
                "execution_time_ms": round(execution_time * 1000, 2),
                "query_type": operation_type
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
        elif natural_language_query.lower().startswith("insert:"):
            query_type = "insert"
            natural_language_query = natural_language_query[7:].strip()
        elif natural_language_query.lower().startswith("update:"):
            query_type = "update"
            natural_language_query = natural_language_query[7:].strip()
        elif natural_language_query.lower().startswith("delete:"):
            query_type = "delete"
            natural_language_query = natural_language_query[7:].strip()
        
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
        operation_type = results.get("query_type", "find")
        execution_time = results.get("execution_time_ms", 0)
        
        # Enhanced prompt for more conversational and copilot-like responses
        # Fixed the {action} placeholder by escaping the curly braces
        prompt = f"""
    You are a helpful MongoDB Copilot assistant who works as a whatsapp chatbot. Explain the results of a database operation in a conversational, helpful manner.

    Original natural language query: "{natural_language_query}"

    MongoDB operation type: {operation_type}

    MongoDB query executed: {json.dumps(mongo_query, indent=2)}

    Number of results: {result_count}

    Sample results (up to 3): {json.dumps(result_sample, indent=2, default=str)}

    Execution time: {execution_time}ms

    Instructions:
    1. Begin with a brief statement about what you did, like "I've {{action}} for you..." where {{action}} relates to the operation type.
    2. For find/aggregate: Describe what was found in conversational terms.
    3. For insert: Confirm what was inserted and provide the new document ID if available.
    4. For update: Explain how many documents were modified and what was changed.
    5. For delete: Confirm how many documents were deleted and why.
    6. For count: Provide the count in a natural sentence.
    7. Include any interesting patterns or notable information from the results.
    8. Keep your response concise and friendly, like a helpful database assistant.
    9. Don't repeat the raw query unless it helps explain something specific.
    10. If there are no results or zero affected documents, explain that in a helpful way.
    11. If appropriate, suggest a follow-up query the user might want to try.

    Your response should sound natural and helpful, like a perfect whatsapp chatbot, who can give whatever the customer asks, make sure you read the result of the query very carefuly and answer the question, you are very smart.
    """
        
        # Generate the explanation
        response = self.model.generate_content(prompt)
        return response.text