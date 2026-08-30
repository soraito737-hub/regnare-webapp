from embedding_classifier import CommentClassifier

classifier = CommentClassifier()
classifier.fit()

text = "めちゃ応援してる！"
vec = classifier._embed(text)

from sklearn.metrics.pairwise import cosine_similarity

for category, vectors in classifier._category_vectors.items():
    sims = cosine_similarity([vec], vectors)[0]
    max_idx = sims.argmax()
    print(f"{category}: 最大類似度={sims[max_idx]:.4f}  (最類似例: {classifier.training_examples[category][max_idx]})")