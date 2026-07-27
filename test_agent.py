import sys
import os

from tania_agent import fresh_state, process_turn

def main():
    state = fresh_state()
    history = []
    
    print("Testing process_turn with local LLM...")
    try:
        state, agent_reply, decision = process_turn("Hello, I want some water", state, history)
        print("Reply:", agent_reply)
        print("Decision:", decision)
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
