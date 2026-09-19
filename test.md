ghghghg
```
for p in $(kubectl get validatingpolicy -o jsonpath='{.items[*].metadata.name}'); do
  kubectl patch validatingpolicy "$p" --type='json' \
    -p='[{"op": "replace", "path": "/spec/validationActions", "value": ["Audit"]}]'
done



```
