# import json
# import os
# import sys

# # Add src to path if needed
# sys.path.append(os.path.join(os.getcwd(), 'src'))

# from tag_read import lambda_handler as tag_read_handler
# from tag_write import lambda_handler as tag_write_handler
# from tag_on_create import lambda_handler as tag_on_create_handler

# def test_tag_read_multi():
#     print("\n--- Testing TagRead (Multi-Region) ---")
#     event = {
#         "resource": "EC2Instance",
#         "regions": ["us-east-1", "us-east-2"],
#         "filters": {
#             "Owner": "jatin.sharma" 
#         }
#     }
#     # Note: This will actually call AWS if credentials are set
#     # Using a try-block to avoid crashing if no resources found or access denied
#     try:
#         res = tag_read_handler(event, None)
#         print(json.dumps(res, indent=2))
#     except Exception as e:
#         print(f"Error: {e}")

# def test_tag_write_multi():
#     print("\n--- Testing TagWrite (Cross-Region ARNs) ---")
#     # Using dummy ARNs that look real enough for the regex/parser
#     event = {
#         "arns": [
#             "arn:aws:ec2:us-east-1:123456789012:instance/i-fake1",
#             "arn:aws:ec2:us-west-2:123456789012:instance/i-fake2"
#         ],
#         "tags": {
#             "ProcessedBy": "MultiRegionTest"
#         }
#     }
#     try:
#         res = tag_write_handler(event, None)
#         print(json.dumps(res, indent=2))
#     except Exception as e:
#         print(f"Error: {e}")

# def test_tag_on_create_scan():
#     print("\n--- Testing TagOnCreate (Scan Mode) ---")
#     event = {
#         "action": "scan",
#         "regions": ["us-east-1"] # Limited to one for faster test
#     }
#     try:
#         res = tag_on_create_handler(event, None)
#         print(json.dumps(res, indent=2))
#     except Exception as e:
#         print(f"Error: {e}")

# def test_tag_on_create_event():
#     print("\n--- Testing TagOnCreate (Event-Driven Multi-ARN) ---")
#     event = {
#         "version": "0",
#         "detail-type": "AWS API Call via CloudTrail",
#         "source": "aws.ec2",
#         "detail": {
#             "eventName": "RunInstances",
#             "awsRegion": "us-east-1",
#             "userIdentity": {
#                 "type": "IAMUser",
#                 "userName": "test-user"
#             },
#             "responseElements": {
#                 "instancesSet": {
#                     "items": [
#                         {"instanceId": "i-001"}
#                     ]
#                 }
#             }
#         }
#     }
#     try:
#         res = tag_on_create_handler(event, None)
#         print(json.dumps(res, indent=2))
#     except Exception as e:
#         print(f"Error: {e}")

# if __name__ == "__main__":
#     # You can uncomment these to run actual tests if you have AWS credentials set up
#     # However, be aware they will perform actual read/write operations.
    
#     print("Multi-Region Testing Utility")
#     print("============================")
    
#     # test_tag_read_multi()
#     # test_tag_write_multi()
#     # test_tag_on_create_scan()
#     # test_tag_on_create_event()
    
#     print("\nTo run these tests, uncomment the function calls in scratch/test_multi_region.py")
#     print("Ensure you have valid AWS credentials configured (e.g. via 'aws configure').")
