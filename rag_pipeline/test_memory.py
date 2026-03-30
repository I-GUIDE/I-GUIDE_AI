from langchain.memory import ConversationBufferMemory
from langchain_agent_executor import run_agent_query

# Create a shared memory object
memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

# First query: Establish context
print("First query:")
result1 = run_agent_query(
    "What is the capital of France?",
    memory=memory,
    verbose=True  # Enable to see agent reasoning
)
print("Response:", result1.get("final_answer", "No answer"))

# Second query: Reference the previous context
print("\nSecond query (referencing previous):")
result2 = run_agent_query(
    "What is its population?",
    memory=memory,  # Same memory object
    verbose=True
)
print("Response:", result2.get("final_answer", "No answer"))

# Check memory contents
print("\nMemory contents:")
print(memory.chat_memory.messages)