import networkx as nx
from collections import defaultdict
from app.models import Viewer

def generate_user_network(messages):
    """
    Analyzes interactions between users.
    Returns nodes and links for D3.js force-directed graph.
    Nodes: Users
    Links: User A mentions User B, or User A and User B chat in close proximity (simple heuristic)
    """
    G = nx.DiGraph()

    if not messages:
        return {"nodes": [], "links": []}

    # Pre-fetch viewer scores
    usernames = set()
    channel_id = messages[0].channel_id if hasattr(messages[0], 'channel_id') else None

    for msg in messages:
        u = msg.username if hasattr(msg, 'username') else msg.get('username', '')
        usernames.add(u)

    viewer_scores = {}
    if channel_id:
        viewers = Viewer.query.filter(Viewer.channel_id == channel_id, Viewer.username.in_(usernames)).all()
        for v in viewers:
            viewer_scores[v.username] = v.suspicion_score

    # 1. Build Graph from Mentions
    # Iterate messages, find @username mentions

    # We also track who is active to add them as nodes even without edges
    active_users = set()

    # Heuristic: Temporal proximity. If users chat within X seconds, create weak link?
    # This might create a too dense graph.
    # Let's stick to Mentions + "Same Content" (Bot Farm indicator)

    msg_content_map = defaultdict(list)

    for msg in messages:
        username = msg.username if hasattr(msg, 'username') else msg.get('username', '')
        content = msg.message if hasattr(msg, 'message') else msg.get('message', '')

        active_users.add(username)
        score = viewer_scores.get(username, 0)

        # Add Node
        if not G.has_node(username):
            G.add_node(username, group=1, score=score) # Group 1: Normal?

        # Detect Mentions (simple regex @\w+)
        import re
        mentions = re.findall(r'@(\w+)', content)
        for mentioned in mentions:
            # We assume mentioned user exists in graph only if we saw them chat?
            # Or add them as potential target
            if not G.has_node(mentioned):
                 # Try to look up score if we have it (might not if they didn't speak in this batch)
                 m_score = viewer_scores.get(mentioned, 0)
                 G.add_node(mentioned, group=2, score=m_score) # Group 2: Target/Mentioned only

            if G.has_edge(username, mentioned):
                G[username][mentioned]['weight'] += 1
            else:
                G.add_edge(username, mentioned, weight=1)

        # Group by content for Bot Farm detection
        # If message is long enough to be significant
        if len(content) > 10:
             msg_content_map[content].append(username)

    # 2. Add "Bot Farm" Edges (High Weight)
    for content, users in msg_content_map.items():
        if len(users) > 1:
            # Connect all users who said the same thing
            # This is O(N^2) for users per message, but usually small N
            unique_users = list(set(users))
            for i in range(len(unique_users)):
                for j in range(i + 1, len(unique_users)):
                    u1 = unique_users[i]
                    u2 = unique_users[j]

                    if G.has_edge(u1, u2):
                        G[u1][u2]['weight'] += 5 # Strong link
                    else:
                        G.add_edge(u1, u2, weight=5)

    # 3. Community Detection (Simple modularity or connected components)
    # We can just pass the graph and let D3 force layout cluster them.
    # But let's identify "Bot Clusters" -> Cliques formed by same-content

    # Format for D3
    nodes = []
    for n in G.nodes(data=True):
        nodes.append({
            "id": n[0],
            "group": n[1].get("group", 1),
            "score": n[1].get("score", 0)
        })

    links = []
    for u, v, data in G.edges(data=True):
        links.append({"source": u, "target": v, "value": data['weight']})

    return {"nodes": nodes, "links": links}
