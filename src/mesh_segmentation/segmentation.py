import bpy
import mathutils
import math
import numpy
import scipy.linalg
import scipy.cluster
import scipy.sparse
import scipy.sparse.csgraph

delta = None
eta = None


def _face_center(mesh, face):
    """Computes the coordinates of the center of the given face"""
    center = mathutils.Vector()
    for vert in face.vertices:
        center += mesh.vertices[vert].co
    return center/len(face.vertices)


def _geodesic_distance(mesh, face1, face2, edge):
    """Computes the geodesic distance over the given edge between 
    the two adjacent faces face1 and face2"""
    edge_center = (mesh.vertices[edge[0]].co + mesh.vertices[edge[1]].co)/2
    return (edge_center - _face_center(mesh, face1)).length + \
           (edge_center - _face_center(mesh, face2)).length


def _angular_distance(face1, face2):
    """Computes the angular distance of the given adjacent faces"""
    return (1 - math.cos(mathutils.Vector.angle(face1.normal, 
                                                face2.normal)))
                                                      
                                                      
def _create_distance_matrices(mesh, save_dists):
    """Creates the matrices of the angular and geodesic distances
    between all adjacent faces. The i,j-th entry of the returned
    matrices contains the distance between the i-th and j-th face.
    
    save_dists = True will calculate the matrices and save them
    into the mesh, so that these can be used in a later call of
    this function with save_dists = False
    """
    
    faces = mesh.polygons
    l = len(faces)
    
    if not save_dists and ("geo_dist_mat" in mesh and "ang_dist_mat" in mesh and
                           "geo_dist_avg" in mesh and "ang_dist_avg" in mesh):
        # the matrices are already calculated, we only have to load and
        # return them
        return (scipy.sparse.lil_matrix(mesh["geo_dist_mat"]),
                scipy.sparse.lil_matrix(mesh["ang_dist_mat"]),
                mesh["geo_dist_avg"],
                mesh["ang_dist_avg"])
    else:
        # matrix of geodesic distances (use lil for fast insertion of entries)
        G = scipy.sparse.lil_matrix((l, l), dtype=float)
        avgG = 0
        # matrix of angular distances
        A = scipy.sparse.lil_matrix((l, l), dtype=float)
        avgA = 0
    
        # progress bar
      #  bpy.context.window_manager.progress_begin(0, 100)
      #  progress = 0
      #  step = 1/len(mesh.edge_keys)
        
        # number of pairs of adjacent faces
        num_adj = 0
    
        # find adjacent faces
        for edge in mesh.edge_keys:
          #  bpy.context.window_manager.progress_update(progress) 
            j = None # index of possible adjacent face
            for i, face in enumerate(faces):
                if edge in face.edge_keys:
                    if not (j is None or G[i,j] != 0):
                        G[i,j] = _geodesic_distance(mesh, face, faces[j], edge)
                        A[i,j] = _angular_distance(face, faces[j])
                        G[j,i] = G[i,j]
                        A[j,i] = A[i,j]
                        if G[i,j] > avgG:
                            avgG += G[i,j]
                        if A[i,j] > avgA:
                            avgA += A[i,j]
                        num_adj += 1
                        break
                    else:
                        j = i
            #progress += step
            
        avgG = avgG/num_adj
        avgA = avgA/num_adj
            
        if save_dists:
            mesh["geo_dist_mat"] = G.toarray()
            mesh["ang_dist_mat"] = A.toarray()
            mesh["geo_dist_avg"] = avgG
            mesh["ang_dist_avg"] = avgA
            
        #bpy.context.window_manager.progress_end()
        
        return (G, A, avgG, avgA)

def _create_affinity_matrix(mesh):
    """Create the adjacency matrix of the given mesh"""
    
    l = len(mesh.polygons)
    G, A, avgG, avgA = _create_distance_matrices(mesh, False)    
    
    # weight with delta and average value
    G = G.dot(delta/avgG)
    A = A.dot((1 - delta)*eta/(avgA * eta))
    
    # for each non adjacent pair of faces find shortest path of adjacent faces 
    W = scipy.sparse.csgraph.dijkstra(G + A, directed = False)
    
    # change distance entries to similarities
    sigma = W.sum()/(l ** 2)
    den = 2 * (sigma ** 2)
    
    for i in range(l):
        W[i,i] = 1
        for j in range(i + 1, l):
            W[i,j] = math.exp(-W[i,j]/den)
            W[j,i] = W[i,j]
            
    return W


def _max_sum_association(Q,chosen,i):
    s = 0
    for j in range(len(chosen)):
        s += Q[chosen[j],i]
    return s

def _initial_guess(Q,k):
    """Computes an initial guess for the cluster-centers"""
    n = Q.shape[0]
    min_value = 2
    min_indices=(-1,-1)
    for i in range(n):
        for j in range(n):
          if not i == j:
             if Q[i,j] < min_value:
                min_value = Q[i,j]
                min_indices=(i,j)
    chosen = [min_indices[0],min_indices[1]]
    for x in range(3,k):
        min_sum = 10000 #set in a better way
        akt_sum = 0
        new_index = -1
        for i in range(n):
            if not i in chosen:
                akt_sum = _max_sum_association(Q,chosen,i)
                if akt_sum < min_sum:
                    min_sum = akt_sum
                    new_index = i
        chosen.append(new_index)
    return chosen

        
        

def segment_mesh(mesh, k, coefficients, action):
    """Segments the given mesh into k clusters and performs the given 
    action for each cluster
    """
    
    # set coefficients
    global delta
    global eta
    delta, eta = coefficients
    
    # affinity matrix
    W = _create_affinity_matrix(mesh)
    # degree matrix
    Dsqrt = numpy.diag([math.sqrt(1/entry) for entry in W.sum(1)])
    # graph laplacian
    L = Dsqrt.dot(W.dot(Dsqrt))
 
    # get eigenvectors
    l,V = scipy.linalg.eigh(L, eigvals = (L.shape[0] - k, L.shape[0] - 1))
    # normalize each column to unit length
    V = V / [numpy.linalg.norm(column) for column in V.transpose()]
    #compute association matrix
    Q = V.dot(V.transpose())
    #compute initial guess for clustering
    initial_clusters = _initial_guess(Q,k)
    # apply kmeans
    cluster_res,_ = scipy.cluster.vq.kmeans(V, V[initial_clusters,:])
    # get identification vector
    idx,_ = scipy.cluster.vq.vq(V, cluster_res)
    
    # perform action with the clustering result
    if action:
        action(mesh, k, idx)
    
