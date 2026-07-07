from agents.workflow import graph

graph = graph.compile()

png = graph.get_graph().draw_mermaid_png()
with open("graph.png", "wb") as f:
    f.write(png)